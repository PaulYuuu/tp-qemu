from virttest import error_context, utils_net, utils_test
from virttest.utils_windows import virtio_win


@error_context.context_aware
def run(test, params, env):
    """
    The rx/tx offload checksum test for windows
    1) start vm
    2) set the netkvm driver parameter value
    3) get the value and compare to target value
    4) start ping test to check nic availability

    param test: the test object
    param params: the test params
    param env: test environment
    """

    def start_test(param_name, param_value):
        """
        Start test. First set netkvm driver parameter 'param_name'
        to value 'param_value'. Then read the current and compare
        to 'param_value' to check identity. Finally conduct a ping
        test to check the nic is available.

        param param_name: the netkvm driver parameter to modify
        param param_value: the value to set to
        """
        error_context.context(f"Start set {param_name} to {param_value}", test.log.info)
        utils_net.set_netkvm_param_value(vm, param_name, param_value)

        test.log.info("Check value after setting %s", param_name)
        cur_value = utils_net.get_netkvm_param_value(vm, param_name)
        if cur_value != param_value:
            err_msg = "Current value: %s is not equal to target value: %s"
            err_msg = err_msg % (cur_value, param_value)
            test.fail(err_msg)

        error_context.context("Start ping test", test.log.info)
        guest_ip = vm.get_address()
        status, output = utils_test.ping(guest_ip, 10, timeout=15)
        if status:
            test.fail(f"Ping returns non-zero value {output}")
        package_lost = utils_test.get_loss_ratio(output)
        if package_lost != 0:
            test.fail(f"Ping test got {package_lost} package lost")

    def _get_driver_version(session):
        """
        Get current installed virtio driver version
        return: a int value of version, e.g. 191
        """
        query_version_cmd = params["query_version_cmd"]
        output = session.cmd_output(query_version_cmd)
        version_str = output.strip().split("=")[1]
        version = version_str.split(".")[-1][0:3]
        return int(version)

    timeout = params.get("timeout", 360)
    param_names = params.get("param_names").split()
    param_values_default = params.get("param_values")
    vm = env.get_vm(params["main_vm"])
    vm.verify_alive()

    session = vm.wait_for_login(timeout=timeout)
    error_context.context(
        "Check if the driver is installed and " "verified", test.log.info
    )
    driver_verifier = params["driver_verifier"]
    session = utils_test.qemu.windrv_check_running_verifier(
        session, vm, test, driver_verifier, timeout
    )
    driver_version = _get_driver_version(session)
    session.close()

    virtio_win.prepare_netkvmco(vm)
    if driver_version <= 189 and "*JumboPacket" in param_names:
        param_names.remove("*JumboPacket")
    elif driver_version > 189 and "MTU" in param_names:
        param_names.remove("MTU")
    for name in param_names:
        attr_name = f"param_values_{name}"
        param_values = params.get(attr_name, param_values_default)
        for value in param_values.split():
            start_test(name, value)
