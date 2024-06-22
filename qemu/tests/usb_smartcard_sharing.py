import os
import re

from avocado.utils import process
from virttest import data_dir, error_context, utils_misc


@error_context.context_aware
def run(test, params, env):
    """
    Test usb smartcard sharing:
    Guest  - user of the smartcard
    Client - owner of the smartcard (use host as client in this case)

    1) Check client configurations
    2) Start smartcard sharing via spice
    3) Check guest configurations
    4) Check the smartcard in guest

    :param test: QEMU test object
    :param params: Dictionary with test parameters
    :param env: Dictionary with test environment.
    """

    def _client_config_check():
        status = True
        err_msg = ""
        gui_group = "Server with GUI"
        out = process.getoutput("yum group list --installed", shell=True)
        obj = re.search(r"(Installed Environment Groups:.*?)^\S", out, re.S | re.M)
        if not obj or gui_group not in obj.group(1):
            gui_groupinstall_cmd = f"yum groupinstall -y '{gui_group}'"
            s, o = process.getstatusoutput(gui_groupinstall_cmd, shell=True)
            if s:
                status = False
                err_msg = f"Fail to install '{gui_group}' on client, "
                err_msg += f"output: {o}"
                return (status, err_msg)

        virt_viewer_cmd = "rpm -q virt-viewer || yum install -y virt-viewer"
        s, o = process.getstatusoutput(virt_viewer_cmd, shell=True)
        if s:
            status = False
            err_msg = "Fail to install 'virt-viewer' on client, "
            err_msg += f"output: {o}"
            return (status, err_msg)

        # unpack fake-smartcard database
        sc_db = params.get("sc_db_tar", "fake-smartcard.tar.gz")
        sc_db_src = os.path.join(data_dir.get_deps_dir("smartcard"), sc_db)
        unpack_sc_db = "mkdir -p {0} && tar -zxvf {1} -C {0}"
        unpack_sc_db = unpack_sc_db.format(sc_db_dst, sc_db_src)
        s, o = process.getstatusoutput(unpack_sc_db, shell=True)
        if s:
            status = False
            err_msg = "Fail to unpack smartcard database on client, "
            err_msg += f"output: {o}"
            return (status, err_msg)

        return (status, err_msg)

    def _guest_config_check():
        status = True
        err_msg = ""
        required_groups = ("Server with GUI", "Smart Card Support")
        s, out = session.cmd_status_output("yum group list --installed")
        test.log.info(out)
        if s:
            status = False
            err_msg = "Fail to get installed group list in guest, "
            err_msg += f"output: {out}"
            return (status, err_msg)
        for group in required_groups:
            if group not in out:
                groupinstall_cmd = f"yum groupinstall -y '{group}'"
                s, o = session.cmd_status_output(groupinstall_cmd, timeout=timeout)
                if s:
                    status = False
                    err_msg = f"Fail to install group '{group}' in guest, "
                    err_msg += f"output: {o}"
                    return (status, err_msg)
        o = session.cmd_output("systemctl status pcscd")
        test.log.info(o)
        if "running" not in o:
            s, o = session.cmd_status_output("sytemctl restart pcscd")
            if s:
                status = False
                err_msg = "Fail to start pcscd in guest, "
                err_msg += f"output: {out}"
                return (status, err_msg)
        return (status, err_msg)

    def _check_sc_in_guest():
        status = True
        err_msg = ""
        o = session.cmd_output("lsusb")
        test.log.info(o)
        if ccid_info not in o:
            status = False
            err_msg = "USB CCID device is not present in guest."
            return (status, err_msg)
        list_certs_cmd = "pkcs11-tool --list-objects --type cert"
        s, o = session.cmd_status_output(list_certs_cmd)
        test.log.info(o)
        if s:
            status = False
            err_msg = "Fail to list certificates on the smartcard."
            return (status, err_msg)
        return (status, err_msg)

    def _start_rv_smartcard():
        def _rv_connection_check():
            rv_pid = process.getoutput(f"pidof {rv_binary}")
            cmd = 'netstat -ptn | grep "^tcp.*127.0.0.1:%s.*ESTABLISHED %s.*"'
            cmd = cmd % (spice_port, rv_pid)
            s, o = process.getstatusoutput(cmd)
            if s:
                return False
            test.log.info("netstat output:\n%s", o)
            return True

        status = True
        err_msg = ""
        rv_binary_path = utils_misc.get_binary(rv_binary, params)
        spice_port = vm.get_spice_var("spice_port")
        rv_args = rv_binary_path + f" spice://localhost:{spice_port} "
        rv_args += f"--spice-smartcard --spice-smartcard-db {sc_db_dst} "
        rv_args += "--spice-smartcard-certificates cert1,cert2,cert3"
        rv_args += " > /dev/null 2>&1"
        rv_thread = utils_misc.InterruptedThread(os.system, (rv_args,))
        rv_thread.start()
        if not utils_misc.wait_for(_rv_connection_check, timeout, 60):
            status = False
            err_msg = f"Fail to establish {rv_binary} connection"
        return (status, err_msg)

    if params.get("display") != "spice":
        test.cancel("Only support spice connection")

    params["usbscdev_name"]
    timeout = params.get("wait_timeout", 600)
    rv_binary = params.get("rv_binary", "remote-viewer")
    sc_db_dst = params.get("sc_db_dst", "/home/fake_smartcard")
    ccid_info = params.get("ccid_info", "Gemalto")

    vm = env.get_vm(params["main_vm"])
    vm.verify_alive()

    error_context.context("Check client configurations", test.log.info)
    s, o = _client_config_check()
    if not s:
        test.error(o)

    error_context.context("Start smartcard sharing via spice", test.log.info)
    s, o = _start_rv_smartcard()
    if not s:
        test.error(o)

    error_context.context("Check guest configurations", test.log.info)
    session = vm.wait_for_login()
    s, o = _guest_config_check()
    if not s:
        test.error(o)

    error_context.context("Check the smartcard in guest", test.log.info)
    s, o = _check_sc_in_guest()
    if not s:
        test.fail(o)

    session.close()
