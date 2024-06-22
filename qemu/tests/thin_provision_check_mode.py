import os

from avocado.utils import genio, process
from avocado.utils import path as utils_path
from virttest import env_process, error_context
from virttest.utils_misc import get_linux_drive_path


@error_context.context_aware
def run(test, params, env):
    """
    Qemu provisioning mode checking test:
    1) load scsi_debug module with lbpu=1 / lbpu=0
    2) boot guest with scsi_debug emulated disk as extra data disk
    3) get provisioning mode of data disk in guest
    4) check provisioning mode

    :param test: kvm test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def get_host_scsi_disk():
        """
        Get  scsi disk which emulated by scsi_debug module.
        """
        cmd = "lsblk -S -n -p|grep scsi_debug"
        status, output = process.getstatusoutput(cmd)
        if status != 0:
            test.fail("Can not get scsi_debug disk on host")

        scsi_disk_info = output.strip().split()
        return scsi_disk_info[1], scsi_disk_info[0]

    def get_provisioning_mode(device, host_id):
        """
        Get disk provisioning_mode, value usually is 'writesame_16' or 'unmap',
        depends on params for scsi_debug module.
        """
        device_name = os.path.basename(device)
        path = f"/sys/block/{device_name}/device/scsi_disk"
        path += f"/{host_id}/provisioning_mode"
        return genio.read_one_line(path).strip()

    def get_guest_provisioning_mode(device):
        """
        Get disk provisioning_mode in guest
        """
        cmd = f"lsblk -S -n {device}"
        status, output = session.cmd_status_output(cmd)
        if status != 0:
            test.fail(f"Can not find device {device} in guest")

        host_id = output.split()[1]
        cmd = (
            f"cat /sys/bus/scsi/devices/{host_id}/scsi_disk/{host_id}/provisioning_mode"
        )

        status, output = session.cmd_status_output(cmd)
        if status == 0:
            return output.strip()

        test.fail(f"Can not get provisioning mode {host_id} in guest")

    utils_path.find_command("lsblk")
    host_scsi_id, disk_name = get_host_scsi_disk()
    provisioning_mode = get_provisioning_mode(disk_name, host_scsi_id)
    test.log.info("Current host provisioning_mode = '%s'", provisioning_mode)

    # prepare params to boot vm with scsi_debug disk.
    vm_name = params["main_vm"]
    data_tag = params["data_tag"]
    target_mode = params["target_mode"]
    disk_serial = params["disk_serial"]
    params["start_vm"] = "yes"
    params[f"image_name_{data_tag}"] = disk_name

    error_context.context(f"boot guest with disk '{disk_name}'", test.log.info)
    # boot guest with scsi_debug disk
    env_process.preprocess_vm(test, params, env, vm_name)
    vm = env.get_vm(vm_name)
    vm.verify_alive()
    timeout = float(params.get("login_timeout", 240))
    session = vm.wait_for_login(timeout=timeout)

    output_path = get_linux_drive_path(session, disk_serial)

    if not output_path:
        test.fail("Can not get output file path in guest.")

    mode = get_guest_provisioning_mode(output_path)
    error_context.context(f"Checking provision mode {mode}", test.log.info)
    if mode != target_mode:
        test.fail("Got unexpected mode:%s", mode)
