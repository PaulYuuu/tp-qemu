import re

from virttest import env_process, error_context


@error_context.context_aware
def run(test, params, env):
    """
    Device bit check test:
    We can set up some properties bits though qemu-kvm command line. This case
    will check if those properties bits set up correctly by monitor command
    'qtree' or inside guest by sysfs(only linux).
    1) Boot up a guest with specific parameter
    2) Verify the relevant bit of the device set correctly in the monitor
       or inside guest(if it is possible)

    :param test: qemu test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    default_value = params.get("default_value", "1 1").split()
    option_add = params.get("option_add", "off off").split()
    options = params.get("options", "indirect_desc event_idx").split()
    options_offset = params.get("options_offset", "28 29").split()
    test_loop = params.get("test_loop", "default").split(";")
    timeout = float(params.get("login_timeout", 240))
    dev_type = params.get("dev_type", "virtio-blk-pci")
    dev_param_name = params.get("dev_param_name", "blk_extra_params")
    dev_pattern = params.get("dev_pattern", f"(dev: {dev_type}.*?)dev:")
    pci_id_pattern = params.get("pci_id_pattern")
    ccw_id_pattern = params.get("ccw_id_pattern")
    convert_dict = {"1": ["on", "true"], "0": ["off", "false"]}
    orig_extra_params = params.get(dev_param_name, "")
    for properties in test_loop:
        if properties != "default":
            properties = properties.strip().split()
            extra_params = orig_extra_params
            for index, value in enumerate(properties):
                if value != default_value[index]:
                    extra_params += f",{options[index]}={option_add[index]}"
            params[dev_param_name] = extra_params.lstrip(",")
        else:
            properties = default_value

        error_context.context(
            f"Boot up guest with properties: {str(options)} value as: {properties}",
            test.log.info,
        )
        vm_name = params["main_vm"]
        params["start_vm"] = "yes"
        env_process.preprocess_vm(test, params, env, vm_name)

        vm = env.get_vm(vm_name)

        session = vm.wait_for_login(timeout=timeout)
        qtree_info = vm.monitor.info("qtree")
        dev_info = re.findall(dev_pattern, qtree_info, re.S)
        if not dev_info:
            test.error("Can't get device info from qtree result.")

        for index, option in enumerate(options):
            option_regex = rf"{option}\s+=\s+(\w+)"
            option_value = re.findall(option_regex, dev_info[0], re.M)
            if not option_value:
                test.log.debug("dev info in qtree: %s", dev_info[0])
                test.error("Can't get the property info from qtree result")
            if option_value[0] not in convert_dict[properties[index]]:
                msg = f"'{option}' value get '{option_value}', "
                msg += f"expect value '{convert_dict[properties[index]]}'"
                test.log.debug(msg)
                test.fail(f"Property bit for {option} is wrong.")

            test.log.info("Property bit in qtree is right for %s.", option)
            if params.get("check_in_guest", "yes") == "yes":
                if params.get("machine_type").startswith("s390"):
                    id_pattern = (
                        ccw_id_pattern
                        + re.findall(
                            "dev:virtio-scsi-ccw.*\n"
                            '.*\n.*\n.*\ndev_id="'
                            'fe.0.(.*?)"',
                            qtree_info.replace(" ", ""),
                        )[0]
                    )
                    ccw_info = session.cmd_output("lscss")
                    ccw_n = re.findall(id_pattern, ccw_info)
                    if not ccw_n:
                        test.error("Can't get the ccw id for device")
                    cmd = f"cat /sys/bus/ccw/devices/{ccw_n[0]}/"
                else:
                    pci_info = session.cmd_output("lspci -n")
                    pci_n = re.findall(pci_id_pattern, pci_info)
                    if not pci_n:
                        test.error("Can't get the pci id for device")
                    cmd = f"cat /sys/bus/pci/devices/0000:{pci_n[0]}/"
                cmd += "virtio*/features"
                bitstr = session.cmd_output(cmd)
                bitstr = re.findall("[01]+", bitstr)[-1]

                if bitstr[int(options_offset[index])] != properties[index]:
                    msg = f"bit string in guest: {bitstr}"
                    msg += f"expect bit string: {properties[index]}"
                    test.log.debug(msg)
                    test.fail(f"Property bit for {option} is wrong" " inside guest.")
            test.log.info("Property bit in qtree is right for %s" " in guest.", option)
        session.close()
        vm.destroy()
