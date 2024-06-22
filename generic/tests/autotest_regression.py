import logging

import aexpect
from autotest.client.shared import error
from virttest import utils_misc


@error.context_aware
def run(test, params, env):
    """
    Autotest regression test:

    Use Virtual Machines to test autotest.

    1) Clone the given guest OS (only Linux) image twice.
    2) Boot 2 VMs (autotest_server_vm and autotest_client_vm)
    4) Install the autotest server in the server vm
    5) Run the unittests
    6) Run the pylint checker
    7) Run a simple client sleeptest
    8) Run a simple server sleeptest
    9) Register the client vm in the autotest server
    10) Schedule a simple job sleeptest in the client. Wait for client reboot.
    11) If any of these steps have failed, fail the test and report the error

    :param test: virt test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    github_repo = "git://github.com/autotest/autotest.git"

    step_failures = []
    autotest_repo = params.get("autotest_repo", github_repo)
    autotest_branch = params["autotest_branch"]
    autotest_commit = params["autotest_commit"]
    password = params["password"]
    autotest_install_timeout = int(params.get("autotest_install_timeout", 1800))
    unittests_run_timeout = int(params.get("unittests_run_timeout", 1800))
    unittests_args = params.get("unittests_args", "")
    pylint_run_timeout = int(params.get("pylint_run_timeout", 1800))
    vm_names = params["vms"].split()
    has_client_vm = len(vm_names) > 1
    server_name = vm_names[0]
    vm_server = env.get_vm(server_name)
    vm_server.verify_alive()
    timeout = float(params.get("login_timeout", 240))
    session_server = vm_server.wait_for_login(timeout=timeout)
    server_ip = vm_server.get_address()

    if has_client_vm:
        client_name = vm_names[1]
        vm_client = env.get_vm(client_name)
        vm_client.verify_alive()
        session_client = vm_client.wait_for_login(timeout=timeout)
        client_ip = vm_client.get_address()

    step1 = "autotest-server-install"
    try:
        installer_file = "install-autotest-server.sh"

        if autotest_repo == github_repo:
            installer_url = (
                f"https://raw.github.com/autotest/autotest/{autotest_branch}"
                f"/contrib/{installer_file}"
            )
        else:
            installer_url = (
                "https://raw.github.com/autotest/autotest/master"
                f"/contrib/{installer_file}"
            )

        # Download the install script and execute it
        download_cmd = (
            "python -c 'from urllib2 import urlopen; "
            f'r = urlopen("{installer_url}"); '
            f'f = open("{installer_file}", "w"); '
            "f.write(r.read())'"
        )
        session_server.cmd(download_cmd)
        permission_cmd = "chmod +x install-autotest-server.sh"
        session_server.cmd(permission_cmd)
        install_cmd = (
            "./install-autotest-server.sh -u Aut0t3st -d Aut0t3st "
            f"-g {autotest_repo} -b {autotest_branch}"
        )
        if autotest_commit:
            install_cmd += f" -c {autotest_commit}"
        session_server.cmd(install_cmd, timeout=autotest_install_timeout)
    except aexpect.ShellCmdError as e:
        for line in e.output.splitlines():
            logging.error(line)
        step_failures.append(step1)
    vm_server.copy_files_from(
        guest_path="/tmp/install-autotest-server*log", host_path=test.resultsdir
    )

    top_commit = None
    try:
        session_server.cmd("test -d /usr/local/autotest/.git")
        session_server.cmd("cd /usr/local/autotest")
        top_commit = session_server.cmd("echo `git log -n 1 --pretty=format:%H`")
        top_commit = top_commit.strip()
        logging.info(
            "Autotest top commit for repo %s, branch %s: %s",
            autotest_repo,
            autotest_branch,
            top_commit,
        )
    except aexpect.ShellCmdError as e:
        for line in e.output.splitlines():
            logging.error(line)

    if top_commit is not None:
        session_server.close()
        session_server = vm_server.wait_for_login(
            timeout=timeout, username="autotest", password="Aut0t3st"
        )

        step2 = "unittests"
        try:
            session_server.cmd("cd /usr/local/autotest")
            session_server.cmd(
                f"utils/unittest_suite.py {unittests_args}",
                timeout=unittests_run_timeout,
            )
        except aexpect.ShellCmdError as e:
            for line in e.output.splitlines():
                logging.error(line)
            step_failures.append(step2)

        step3 = "pylint"
        try:
            session_server.cmd("cd /usr/local/autotest")
            session_server.cmd(
                "utils/check_patch.py --full --yes", timeout=pylint_run_timeout
            )
        except aexpect.ShellCmdError as e:
            for line in e.output.splitlines():
                logging.error(line)
            step_failures.append(step3)

        step4 = "client_run"
        try:
            session_server.cmd("cd /usr/local/autotest/client")
            session_server.cmd(
                "./autotest-local run sleeptest", timeout=pylint_run_timeout
            )
            session_server.cmd("rm -rf results/default")
        except aexpect.ShellCmdError as e:
            for line in e.output.splitlines():
                logging.error(line)
            step_failures.append(step4)

        if has_client_vm:
            step5 = "server_run"
            try:
                session_client.cmd("iptables -F")
                session_server.cmd("cd /usr/local/autotest")
                session_server.cmd(
                    f"server/autotest-remote -m {client_ip} --ssh-user root "
                    f"--ssh-pass {password} "
                    "-c client/tests/sleeptest/control",
                    timeout=pylint_run_timeout,
                )
                session_server.cmd("rm -rf results-*")
            except aexpect.ShellCmdError as e:
                for line in e.output.splitlines():
                    logging.error(line)
                step_failures.append(step5)

            step6 = "registering_client_cli"
            try:
                label_name = f"label-{utils_misc.generate_random_id()}"
                create_label_cmd = (
                    "/usr/local/autotest/cli/autotest-rpc-client "
                    f"label create -t {label_name} -w {server_ip}"
                )
                session_server.cmd(create_label_cmd)

                list_labels_cmd = (
                    "/usr/local/autotest/cli/autotest-rpc-client "
                    f"label list -a -w {server_ip}"
                )
                list_labels_output = session_server.cmd(list_labels_cmd)
                for line in list_labels_output.splitlines():
                    logging.debug(line)
                if label_name not in list_labels_output:
                    raise ValueError(
                        f"No label {label_name} in the output of {list_labels_cmd}"
                    )

                create_host_cmd = (
                    "/usr/local/autotest/cli/autotest-rpc-client "
                    f"host create -t {label_name} {client_ip} -w {server_ip}"
                )
                session_server.cmd(create_host_cmd)

                list_hosts_cmd = (
                    "/usr/local/autotest/cli/autotest-rpc-client "
                    f"host list -w {server_ip}"
                )
                list_hosts_output = session_server.cmd(list_hosts_cmd)
                for line in list_hosts_output.splitlines():
                    logging.debug(line)
                if client_ip not in list_hosts_output:
                    raise ValueError(
                        f"No client {client_ip} in the output of {create_label_cmd}"
                    )
                if label_name not in list_hosts_output:
                    raise ValueError(
                        f"No label {label_name} in the output of {create_label_cmd}"
                    )

            except (aexpect.ShellCmdError, ValueError) as e:
                if isinstance(e, aexpect.ShellCmdError):
                    for line in e.output.splitlines():
                        logging.error(line)
                elif isinstance(e, ValueError):
                    logging.error(e)
                step_failures.append(step6)

            step7 = "running_job_cli"
            try:
                session_client.cmd("iptables -F")

                job_name = f"Sleeptest {utils_misc.generate_random_id()}"

                def job_is_status(status):
                    list_jobs_cmd = (
                        "/usr/local/autotest/cli/autotest-rpc-client "
                        f"job list -a -w {server_ip}"
                    )
                    list_jobs_output = session_server.cmd(list_jobs_cmd)
                    if job_name in list_jobs_output:
                        if status in list_jobs_output:
                            return True
                        elif "Aborted" in list_jobs_output:
                            raise ValueError("Job is in aborted state")
                        elif "Failed" in list_jobs_output:
                            raise ValueError("Job is in failed state")
                        else:
                            return False
                    else:
                        raise ValueError(
                            f"Job {job_name} does not show in the "
                            f"output of {list_jobs_cmd}"
                        )

                def job_is_completed():
                    return job_is_status("Completed")

                def job_is_running():
                    return job_is_status("Running")

                job_create_cmd = (
                    "/usr/local/autotest/cli/autotest-rpc-client "
                    f"job create --test sleeptest -m {client_ip} '{job_name}' -w {server_ip}"
                )
                session_server.cmd(job_create_cmd)

                if not utils_misc.wait_for(
                    job_is_running, 300, 0, 10, "Waiting for job to start running"
                ):
                    raise ValueError("Job did not start running")

                # Wait for the session to become unresponsive
                if not utils_misc.wait_for(
                    lambda: not session_client.is_responsive(), timeout=300
                ):
                    raise ValueError("Client machine did not reboot")

                # Establish a new client session
                session_client = vm_client.wait_for_login(timeout=timeout)

                # Wait for the job to complete
                if not utils_misc.wait_for(
                    job_is_completed, 300, 0, 10, "Waiting for job to complete"
                ):
                    raise ValueError("Job did not complete")

                # Copy logs back so we can analyze them
                vm_server.copy_files_from(
                    guest_path="/usr/local/autotest/results/*",
                    host_path=test.resultsdir,
                )

            except (aexpect.ShellCmdError, ValueError) as e:
                if isinstance(e, aexpect.ShellCmdError):
                    for line in e.output.splitlines():
                        logging.error(line)
                elif isinstance(e, ValueError):
                    logging.error(e)
                step_failures.append(step7)

    def report_version():
        if top_commit is not None:
            logging.info("Autotest git repo: %s", autotest_repo)
            logging.info("Autotest git branch: %s", autotest_repo)
            logging.info("Autotest top commit: %s", top_commit)

    if step_failures:
        logging.error("The autotest regression testing failed")
        report_version()
        raise error.TestFail(
            "The autotest regression testing had the "
            f"following steps failed: {step_failures}"
        )
    else:
        logging.info("The autotest regression testing passed")
        report_version()
