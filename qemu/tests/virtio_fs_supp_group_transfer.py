import logging
import os.path

from avocado.utils import process
from virttest import data_dir, env_process, error_context, nfs, utils_disk, utils_misc

from provider import virtio_fs_utils

LOG_JOB = logging.getLogger("avocado.test")


@error_context.context_aware
def run(test, params, env):
    """
    Grant write access at users allowed by group permission
    on shared dir( linux only )

    1) Run the virtiofsd daemon on the host and Set log level to "debug".
    2) Boot a guest on the host.
    3) Log into guest with root, then mount the file system.
    4) In the guest, add a new user(u1)
       and set the user to the wheel group (as supplementary group)
       and set the dir's group to 'wheel'
    5) In guest, using root to create a test directory at the shared dir.
    6) Run the basic io test with the u1 user
    7) After test, clear the environment.

    :param test: QEMU test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment
    """

    def _basic_io_test(test, params, session, fs_dest, fs_source):
        """
        Virtio_fs basic io test. Create file on guest and then compare two md5
        values from guest and host.

        :param test: QEMU test object
        :param params: Dictionary with the test parameters
        :param session: the session from guest
        :param fs_dest: the destination path at guest
        :param fs_source: the component of path at host
        """
        error_context.context("Running viofs basic io test", LOG_JOB.info)
        test_file = params.get("virtio_fs_test_file", "virtio_fs_test_file")
        windows = params.get("os_type", "windows") == "windows"
        io_timeout = params.get_numeric("fs_io_timeout", 120)
        fs_target = params.get("fs_target")
        base_dir = params.get("fs_source_base_dir", data_dir.get_data_dir())
        if not os.path.isabs(fs_source):
            fs_source = os.path.join(base_dir, fs_source)
        host_data = os.path.join(fs_source, test_file)
        try:
            if windows:
                cmd_dd = params.get(
                    "virtio_fs_cmd_dd", "dd if=/dev/random of=%s bs=1M count=100"
                )
                driver_letter = virtio_fs_utils.get_virtiofs_driver_letter(
                    test, fs_target, session
                )
                # replace the value if platform is windows
                fs_dest = f"{driver_letter}:"
            else:
                cmd_dd = params.get(
                    "virtio_fs_cmd_dd",
                    "dd if=/dev/urandom of=%s bs=1M " "count=100 iflag=fullblock",
                )
            guest_file = os.path.join(fs_dest, test_file)
            error_context.context(
                f"The guest file in shared dir is {guest_file}", LOG_JOB.info
            )
            error_context.context(
                f"Creating file under {fs_dest} inside guest.", LOG_JOB.info
            )
            session.cmd(cmd_dd % guest_file, io_timeout)

            if windows:
                guest_file_win = guest_file.replace("/", "\\")
                cmd_md5 = params.get("cmd_md5", "%s: && md5sum.exe %s")
                cmd_md5_vm = cmd_md5 % (driver_letter, guest_file_win)
            else:
                cmd_md5 = params.get("cmd_md5", "md5sum %s")
                cmd_md5_vm = cmd_md5 % guest_file
            md5_guest = session.cmd_output(cmd_md5_vm, io_timeout).strip().split()[0]
            error_context.context(f"md5 of the guest file: {md5_guest}", LOG_JOB.info)
            md5_host = (
                process.run(f"md5sum {host_data}", io_timeout)
                .stdout_text.strip()
                .split()[0]
            )
            error_context.context(f"md5 of the host file: {md5_host}", LOG_JOB.info)
            if md5_guest != md5_host:
                test.fail("The md5 value of host is not same to guest.")
            else:
                error_context.context(
                    "The md5 of host is as same as md5 of " "guest.", LOG_JOB.info
                )
        finally:
            if not windows:
                session.cmd(f"rm -rf {guest_file}")

        virtio_fs_utils.create_sub_folder_test(params, session, fs_dest, fs_source)

    add_user_cmd = params.get("add_user_cmd")
    del_user_cmd = params.get("del_user_cmd")
    username = params.get("new_guest_user")
    fs_dest = params.get("fs_dest")
    fs_target = params.get("fs_target")
    # nfs config
    setup_local_nfs = params.get("setup_local_nfs")

    testdir = "testdir"
    guest_root_session = None
    vm = None
    nfs_local = None

    try:
        shared_dir = params.get("fs_source_dir")

        if setup_local_nfs:
            # delete the slash at the end
            params["nfs_mount_dir"] = shared_dir[:-1]
            nfs_local = nfs.Nfs(params)
            nfs_local.setup()

        params["start_vm"] = "yes"
        env_process.preprocess_vm(test, params, env, params["main_vm"])
        vm = env.get_vm(params.get("main_vm"))
        vm.verify_alive()
        guest_root_session = vm.wait_for_login()

        error_context.context(
            f"Create a destination directory {fs_dest} " "inside guest.",
            test.log.info,
        )
        if not utils_misc.make_dirs(fs_dest, session=guest_root_session):
            test.fail("Creating directory was failed!")
        error_context.context(
            f"Mount virtiofs target {fs_target} to {fs_dest} inside" " guest.",
            test.log.info,
        )
        if not utils_disk.mount(
            fs_target, fs_dest, "virtiofs", session=guest_root_session
        ):
            test.fail("Mount virtiofs target failed.")

        error_context.context("Create a common user...", test.log.info)
        guest_root_session.cmd(add_user_cmd % username)
        error_context.context("Change the group to wheel", test.log.info)
        guest_root_session.cmd(f"usermod -G wheel {username}")

        error_context.context(
            "Create a dir inside the virtiofs and " "change it's group to wheel",
            test.log.info,
        )
        guest_root_session.cmd(f"cd {fs_dest} && mkdir -m 770 {testdir}")
        guest_root_session.cmd(f"cd {fs_dest} && chgrp wheel {testdir}")

        error_context.context(
            "Login the common user and try to write under "
            "the dir which belongs to wheel group",
            test.log.info,
        )
        guest_user_session = vm.wait_for_login()
        guest_user_session.cmd(f"su {username}")
        _basic_io_test(
            test,
            params,
            guest_user_session,
            os.path.join(fs_dest, testdir),
            os.path.join(shared_dir, testdir),
        )
    finally:
        if guest_root_session:
            output = guest_root_session.cmd_output(del_user_cmd % username)
            if "is currently used by process" in output:
                error_context.context(
                    "Kill process before delete user...", test.log.info
                )
                pid = output.split(" ")[-1]
                guest_root_session.cmd_output(f"kill -9 {pid}")
            guest_root_session.cmd(f"rm -rf /home/{username}")

        if setup_local_nfs:
            if vm and vm.is_alive():
                vm.destroy()
            nfs_local.cleanup()
