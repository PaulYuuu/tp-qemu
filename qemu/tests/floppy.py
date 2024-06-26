import os
import re
import sys
import time

import aexpect
from avocado.utils import process
from virttest import data_dir, env_process, error_context
from virttest.utils_test.qemu import migration


@error_context.context_aware
def run(test, params, env):
    """
    Test virtual floppy of guest:

    1) Create a floppy disk image on host
    2) Start the guest with this floppy image.
    3) Make a file system on guest virtual floppy.
    4) Calculate md5sum value of a file and copy it into floppy.
    5) Verify whether the md5sum does match.

    :param test: QEMU test object.
    :param params: Dictionary with the test parameters.
    :param env: Dictionary with test environment.
    """
    source_file = params["source_file"]
    dest_file = params["dest_file"]
    login_timeout = int(params.get("login_timeout", 360))
    floppy_prepare_timeout = int(params.get("floppy_prepare_timeout", 360))
    guest_floppy_path = params["guest_floppy_path"]

    def create_floppy(params, prepare=True):
        """
        Creates 'new' floppy with one file on it

        :param params: parameters for test
        :param prepare: if True then it prepare cd images.

        :return: path to new floppy file.
        """
        error_context.context("creating test floppy", test.log.info)
        floppy = params["floppy_name"]
        if not os.path.isabs(floppy):
            floppy = os.path.join(data_dir.get_data_dir(), floppy)
        if prepare:
            process.run(f"dd if=/dev/zero of={floppy} bs=512 count=2880")
        return floppy

    def cleanup_floppy(path):
        """Removes created floppy"""
        error_context.context("cleaning up temp floppy images", test.log.info)
        os.remove(f"{path}")

    def lazy_copy(vm, dst_path, check_path, copy_timeout=None, dsize=None):
        """
        Start disk load. Cyclic copy from src_path to dst_path.

        :param vm: VM where to find a disk.
        :param src_path: Source of data
        :param copy_timeout: Timeout for copy
        :param dsize: Size of data block which is periodically copied.
        """
        if copy_timeout is None:
            copy_timeout = 120
        session = vm.wait_for_login(timeout=login_timeout)
        cmd = (
            'nohup bash -c "while [ true ]; do echo "1" | '
            f'tee -a {check_path} >> {dst_path}; sleep 0.1; done" 2> /dev/null &'
        )
        pid = re.search(r"\[.+\] (.+)", session.cmd_output(cmd, timeout=copy_timeout))
        return pid.group(1)

    class MiniSubtest:
        def __new__(cls, *args, **kargs):
            self = super().__new__(cls)
            ret = None
            exc_info = None
            if args is None:
                args = []
            try:
                try:
                    ret = self.test(*args, **kargs)
                except Exception:
                    exc_info = sys.exc_info()
            finally:
                if hasattr(self, "clean"):
                    try:
                        self.clean()
                    except Exception:
                        if exc_info is None:
                            raise
                    if exc_info:
                        raise exc_info[1].with_traceback(exc_info[2])
            return ret

    class test_singlehost(MiniSubtest):
        def test(self):
            create_floppy(params)
            params["start_vm"] = "yes"
            vm_name = params.get("main_vm", "vm1")
            env_process.preprocess_vm(test, params, env, vm_name)
            vm = env.get_vm(vm_name)
            vm.verify_alive()
            self.session = vm.wait_for_login(timeout=login_timeout)

            self.dest_dir = params.get("mount_dir")
            # If mount_dir specified, treat guest as a Linux OS
            # Some Linux distribution does not load floppy at boot and Windows
            # needs time to load and init floppy driver
            if self.dest_dir:
                lsmod = self.session.cmd("lsmod")
                if "floppy" not in lsmod:
                    self.session.cmd("modprobe floppy")
            else:
                time.sleep(20)

            error_context.context("Formatting floppy disk before using it")
            format_cmd = params["format_floppy_cmd"]
            self.session.cmd(format_cmd, timeout=120)
            test.log.info("Floppy disk formatted successfully")

            if self.dest_dir:
                error_context.context("Mounting floppy")
                self.session.cmd(f"mount {guest_floppy_path} {self.dest_dir}")
            error_context.context("Testing floppy")
            self.session.cmd(params["test_floppy_cmd"])

            error_context.context("Copying file to the floppy")
            md5_cmd = params.get("md5_cmd")
            if md5_cmd:
                md5_source = self.session.cmd(f"{md5_cmd} {source_file}")
                try:
                    md5_source = md5_source.split(" ")[0]
                except IndexError:
                    test.error(
                        "Failed to get md5 from source file," f" output: '{md5_source}'"
                    )
            else:
                md5_source = None

            self.session.cmd(
                "{} {} {}".format(params["copy_cmd"], source_file, dest_file)
            )
            test.log.info("Succeed to copy file '%s' into floppy disk", source_file)

            error_context.context("Checking if the file is unchanged " "after copy")
            if md5_cmd:
                md5_dest = self.session.cmd(f"{md5_cmd} {dest_file}")
                try:
                    md5_dest = md5_dest.split(" ")[0]
                except IndexError:
                    test.error(
                        "Failed to get md5 from dest file," f" output: '{md5_dest}'"
                    )
                if md5_source != md5_dest:
                    test.fail("File changed after copy to floppy")
            else:
                md5_dest = None
                self.session.cmd(
                    "{} {} {}".format(params["diff_file_cmd"], source_file, dest_file)
                )

        def clean(self):
            clean_cmd = "{} {}".format(params["clean_cmd"], dest_file)
            self.session.cmd(clean_cmd)
            if self.dest_dir:
                self.session.cmd(f"umount {self.dest_dir}")
            self.session.close()

    class Multihost(MiniSubtest):
        def test(self):
            error_context.context(
                "Preparing migration env and floppies.", test.log.info
            )
            mig_protocol = params.get("mig_protocol", "tcp")
            self.mig_type = migration.MultihostMigration
            if mig_protocol == "fd":
                self.mig_type = migration.MultihostMigrationFd
            if mig_protocol == "exec":
                self.mig_type = migration.MultihostMigrationExec
            if "rdma" in mig_protocol:
                self.mig_type = migration.MultihostMigrationRdma

            self.vms = params.get("vms").split(" ")
            self.srchost = params["hosts"][0]
            self.dsthost = params["hosts"][1]
            self.is_src = params["hostid"] == self.srchost
            self.mig = self.mig_type(
                test,
                params,
                env,
                False,
            )

            if self.is_src:
                vm = env.get_vm(self.vms[0])
                vm.destroy()
                self.floppy = create_floppy(params)
                self.floppy_dir = os.path.dirname(self.floppy)
                params["start_vm"] = "yes"
                env_process.process(
                    test,
                    params,
                    env,
                    env_process.preprocess_image,
                    env_process.preprocess_vm,
                )
                vm = env.get_vm(self.vms[0])
                vm.wait_for_login(timeout=login_timeout)
            else:
                self.floppy = create_floppy(params, False)
                self.floppy_dir = os.path.dirname(self.floppy)

        def clean(self):
            self.mig.cleanup()
            if self.is_src:
                cleanup_floppy(self.floppy)

    class test_multihost_write(Multihost):
        def test(self):
            from autotest.client.shared.syncdata import SyncData

            super().test()

            copy_timeout = int(params.get("copy_timeout", 480))
            self.mount_dir = params["mount_dir"]
            format_floppy_cmd = params["format_floppy_cmd"]
            check_copy_path = params["check_copy_path"]

            pid = None
            sync_id = {"src": self.srchost, "dst": self.dsthost, "type": "file_trasfer"}
            filename = "orig"
            src_file = os.path.join(self.mount_dir, filename)

            if self.is_src:  # Starts in source
                vm = env.get_vm(self.vms[0])
                session = vm.wait_for_login(timeout=login_timeout)

                if self.mount_dir:
                    session.cmd(f"rm -f {src_file}")
                    session.cmd(f"rm -f {check_copy_path}")
                # If mount_dir specified, treat guest as a Linux OS
                # Some Linux distribution does not load floppy at boot
                # and Windows needs time to load and init floppy driver
                error_context.context("Prepare floppy for writing.", test.log.info)
                if self.mount_dir:
                    lsmod = session.cmd("lsmod")
                    if "floppy" not in lsmod:
                        session.cmd("modprobe floppy")
                else:
                    time.sleep(20)

                session.cmd(format_floppy_cmd)

                error_context.context("Mount and copy data.", test.log.info)
                if self.mount_dir:
                    session.cmd(
                        f"mount {guest_floppy_path} {self.mount_dir}", timeout=30
                    )

                error_context.context("File copying test.", test.log.info)

                pid = lazy_copy(vm, src_file, check_copy_path, copy_timeout)

            sync = SyncData(
                self.mig.master_id(),
                self.mig.hostid,
                self.mig.hosts,
                sync_id,
                self.mig.sync_server,
            )

            pid = sync.sync(pid, timeout=floppy_prepare_timeout)[self.srchost]

            self.mig.migrate_wait([self.vms[0]], self.srchost, self.dsthost)

            if not self.is_src:  # Starts in destination
                vm = env.get_vm(self.vms[0])
                session = vm.wait_for_login(timeout=login_timeout)
                error_context.context("Wait for copy finishing.", test.log.info)
                status = session.cmd_status(f"kill {pid}", timeout=copy_timeout)
                if status != 0:
                    test.fail(
                        "Copy process was terminatted with" f" error code {status}"
                    )

                session.cmd_status(f"kill -s SIGINT {pid}", timeout=copy_timeout)

                error_context.context("Check floppy file checksum.", test.log.info)
                md5_cmd = params.get("md5_cmd", "md5sum")
                if md5_cmd:
                    md5_floppy = session.cmd(f"{md5_cmd} {src_file}")
                    try:
                        md5_floppy = md5_floppy.split(" ")[0]
                    except IndexError:
                        test.error(
                            "Failed to get md5 from source file,"
                            f" output: '{md5_floppy}'"
                        )
                    md5_check = session.cmd(f"{md5_cmd} {check_copy_path}")
                    try:
                        md5_check = md5_check.split(" ")[0]
                    except IndexError:
                        test.error(
                            "Failed to get md5 from dst file,"
                            f" output: '{md5_floppy}'"
                        )
                    if md5_check != md5_floppy:
                        test.fail(
                            "There is mistake in copying, "
                            "it is possible to check file on vm."
                        )

                session.cmd(f"rm -f {src_file}")
                session.cmd(f"rm -f {check_copy_path}")

            self.mig._hosts_barrier(
                self.mig.hosts, self.mig.hosts, "finish_floppy_test", login_timeout
            )

        def clean(self):
            super().clean()

    class test_multihost_eject(Multihost):
        def test(self):
            from autotest.client.shared.syncdata import SyncData

            super().test()

            self.mount_dir = params.get("mount_dir", None)
            format_floppy_cmd = params["format_floppy_cmd"]
            floppy = params["floppy_name"]
            second_floppy = params["second_floppy_name"]
            if not os.path.isabs(floppy):
                floppy = os.path.join(data_dir.get_data_dir(), floppy)
            if not os.path.isabs(second_floppy):
                second_floppy = os.path.join(data_dir.get_data_dir(), second_floppy)
            if not self.is_src:
                self.floppy = create_floppy(params)

            pid = None
            sync_id = {"src": self.srchost, "dst": self.dsthost, "type": "file_trasfer"}
            filename = "orig"
            src_file = os.path.join(self.mount_dir, filename)

            if self.is_src:  # Starts in source
                vm = env.get_vm(self.vms[0])
                session = vm.wait_for_login(timeout=login_timeout)

                if self.mount_dir:  # If linux
                    session.cmd(f"rm -f {src_file}")
                # If mount_dir specified, treat guest as a Linux OS
                # Some Linux distribution does not load floppy at boot
                # and Windows needs time to load and init floppy driver
                error_context.context("Prepare floppy for writing.", test.log.info)
                if self.mount_dir:  # If linux
                    lsmod = session.cmd("lsmod")
                    if "floppy" not in lsmod:
                        session.cmd("modprobe floppy")
                else:
                    time.sleep(20)

                if floppy not in vm.monitor.info("block"):
                    test.fail("Wrong floppy image is placed in vm.")

                try:
                    session.cmd(format_floppy_cmd)
                except aexpect.ShellCmdError as e:
                    if e.status == 1:
                        test.log.error(
                            "First access to floppy failed, "
                            " Trying a second time as a workaround"
                        )
                        session.cmd(format_floppy_cmd)

                error_context.context("Check floppy")
                if self.mount_dir:  # If linux
                    session.cmd(
                        f"mount {guest_floppy_path} {self.mount_dir}", timeout=30
                    )
                    session.cmd(f"umount {self.mount_dir}", timeout=30)

                written = None
                if self.mount_dir:
                    filepath = os.path.join(self.mount_dir, "test.txt")
                    session.cmd(f"echo 'test' > {filepath}")
                    output = session.cmd(f"cat {filepath}")
                    written = "test\n"
                else:  # Windows version.
                    filepath = "A:\\test.txt"
                    session.cmd(f"echo test > {filepath}")
                    output = session.cmd(f"type {filepath}")
                    written = "test \n\n"
                if output != written:
                    test.fail(
                        "Data read from the floppy differs"
                        "from the data written to it."
                        f" EXPECTED: {repr(written)} GOT: {repr(output)}"
                    )

                error_context.context("Change floppy.")
                vm.monitor.cmd("eject floppy0")
                vm.monitor.cmd(f"change floppy {second_floppy}")
                session.cmd(format_floppy_cmd)

                error_context.context("Mount and copy data")
                if self.mount_dir:  # If linux
                    session.cmd(
                        f"mount {guest_floppy_path} {self.mount_dir}", timeout=30
                    )

                if second_floppy not in vm.monitor.info("block"):
                    test.fail("Wrong floppy image is placed in vm.")

            sync = SyncData(
                self.mig.master_id(),
                self.mig.hostid,
                self.mig.hosts,
                sync_id,
                self.mig.sync_server,
            )

            pid = sync.sync(pid, timeout=floppy_prepare_timeout)[self.srchost]

            self.mig.migrate_wait([self.vms[0]], self.srchost, self.dsthost)

            if not self.is_src:  # Starts in destination
                vm = env.get_vm(self.vms[0])
                session = vm.wait_for_login(timeout=login_timeout)
                written = None
                if self.mount_dir:
                    filepath = os.path.join(self.mount_dir, "test.txt")
                    session.cmd(f"echo 'test' > {filepath}")
                    output = session.cmd(f"cat {filepath}")
                    written = "test\n"
                else:  # Windows version.
                    filepath = "A:\\test.txt"
                    session.cmd(f"echo test > {filepath}")
                    output = session.cmd(f"type {filepath}")
                    written = "test \n\n"
                if output != written:
                    test.fail(
                        "Data read from the floppy differs"
                        "from the data written to it."
                        f" EXPECTED: {repr(written)} GOT: {repr(output)}"
                    )

            self.mig._hosts_barrier(
                self.mig.hosts, self.mig.hosts, "finish_floppy_test", login_timeout
            )

        def clean(self):
            super().clean()

    test_type = params.get("test_type", "test_singlehost")
    if test_type in locals():
        tests_group = locals()[test_type]
        tests_group()
    else:
        test.fail(
            f"Test group '{test_type}' is not defined in"
            " migration_with_dst_problem test"
        )
