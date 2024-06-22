import re

from avocado.utils import process
from virttest import error_context, utils_misc, utils_package


@error_context.context_aware
def run(test, params, env):
    """
    Run htm cases:
    Case one
    1) Download unit test suite and configure it
    2) Run kvm test on host
    3) Check host is still available

    Case two
    1) Download test application in the guest
    2) Run it in the guest

    :param test: QEMU test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    if params["unit_test"] == "yes":
        error_context.context("Prepare unit test on host", test.log.info)
        cmds = [params["get_htm_dir"], params["compile_htm"]]
        for cmd in cmds:
            s, o = process.getstatusoutput(cmd, timeout=3600)
            if s:
                test.error(f"Failed to run cmd '{cmd}', output: {o}")
        error_context.context("Run htm unit test on host", test.log.info)
        s, o = process.getstatusoutput(params["run_htm_test"], timeout=3600)
        if s:
            test.fail(f"Run htm unit test failed, output: {o}")
        # Make sure if host is available by do commands on host
        status, output = process.getstatusoutput("rm -rf {}".format(params["htm_dir"]))
        if status:
            test.fail(f"Please check host's status: {output}")
        utils_misc.verify_dmesg()
    else:
        check_exist_cmd = params["check_htm_env"]
        s, o = process.getstatusoutput(check_exist_cmd)
        if s:
            test.error(
                f"Please check htm is supported or not by '{check_exist_cmd}', output: {o}"
            )
        vm = env.get_vm(params["main_vm"])
        session = vm.wait_for_login()
        pkgs = params["depends_pkgs"].split()
        if not utils_package.package_install(pkgs, session):
            test.error("Install dependency packages failed")
        session.cmd(params["get_htm_dir"])
        download_htm_demo = params["download_htm_demo"]
        status = session.cmd_status(f"wget {download_htm_demo}")
        if status:
            test.error(
                f"Failed to download test file, please configure it in cfg : {download_htm_demo}"
            )
        else:
            status, output = session.cmd_status_output(params["test_htm_command"])
            if not re.search(params["expected_htm_test_result"], output):
                test.fail(f"Test failed and please check : {output}")
        vm.verify_kernel_crash()
