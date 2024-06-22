import logging
import re

from avocado.utils import crypto, process
from virttest import error_context, utils_misc

from provider.blockdev_snapshot_base import BlockDevSnapshotTest
from qemu.tests.qemu_guest_agent import QemuGuestAgentBasicCheckWin

LOG_JOB = logging.getLogger("avocado.test")


class QemuGuestAgentSnapshotTest(QemuGuestAgentBasicCheckWin):
    def __init__(self, test, params, env):
        super().__init__(test, params, env)
        self.snapshot_create = BlockDevSnapshotTest(self.test, self.params, self.env)

    @error_context.context_aware
    def setup(self, test, params, env):
        # pylint: disable=E1003
        if params["os_type"] == "windows":
            super().setup(test, params, env)
        else:
            super(QemuGuestAgentBasicCheckWin, self).setup(test, params, env)

    @error_context.context_aware
    def _action_before_fsfreeze(self, *args):
        copy_timeout = int(self.params.get("copy_timeoout", 600))
        file_size = int(self.params.get("file_size", "1024"))
        tmp_name = utils_misc.generate_random_string(5)
        self.host_path = self.guest_path = f"/tmp/{tmp_name}"
        if self.params.get("os_type") != "linux":
            self.guest_path = rf"c:\{tmp_name}"

        error_context.context("Create a file in host.")
        process.run(f"dd if=/dev/urandom of={self.host_path} bs=1M count={file_size}")
        self.orig_hash = crypto.hash_file(self.host_path)
        error_context.context(
            f"Transfer file from {self.host_path} to {self.guest_path}",
            LOG_JOB.info,
        )
        self.bg = utils_misc.InterruptedThread(
            self.vm.copy_files_to,
            (self.host_path, self.guest_path),
            dict(verbose=True, timeout=copy_timeout),
        )
        self.bg.start()

    def check_snapshot(self):
        """
        Check whether the snapshot is created successfully.
        """
        snapshot_info = str(self.vm.monitor.info("block"))
        snapshot_node_name = self.params.get("snapshot_node_name")
        if self.params.get("snapshot_file") not in snapshot_info:
            self.test.fail(f"Snapshot doesn't exist:{snapshot_info}")
        LOG_JOB.info("Found snapshot in guest")
        if snapshot_node_name:
            match_string = f"u?'node-name': u?'{snapshot_node_name}'"
            if not re.search(match_string, snapshot_info):
                self.test.fail(
                    f"Can not find node name {snapshot_node_name} of"
                    f" snapshot in block info {snapshot_info}"
                )
            LOG_JOB.info("Match node-name if they are same with expected")

    def cleanup(self, test, params, env):
        super().cleanup(test, params, env)
        self.snapshot_create.snapshot_image.remove()

    @error_context.context_aware
    def _action_after_fsfreeze(self, *args):
        if self.bg.is_alive():
            image_tag = self.params.get("image_name", "image1")
            self.params.object_params(image_tag)

            error_context.context("Creating snapshot", LOG_JOB.info)
            self.snapshot_create.prepare_snapshot_file()
            self.snapshot_create.create_snapshot()
            error_context.context(
                "Checking snapshot created successfully", LOG_JOB.info
            )
            self.check_snapshot()

    @error_context.context_aware
    def _action_before_fsthaw(self, *args):
        pass

    @error_context.context_aware
    def _action_after_fsthaw(self, *args):
        if self.bg:
            self.bg.join()
        # Make sure the returned file is identical to the original one
        try:
            self.host_path_returned = f"{self.host_path}-returned"
            self.vm.copy_files_from(self.guest_path, self.host_path_returned)
            error_context.context("comparing hashes", LOG_JOB.info)
            self.curr_hash = crypto.hash_file(self.host_path_returned)
            if self.orig_hash != self.curr_hash:
                self.test.fail(
                    f"Current file hash ({self.curr_hash}) differs from "
                    f"original one ({self.orig_hash})"
                )
        finally:
            error_context.context("Delete the created files.", LOG_JOB.info)
            process.run(f"rm -rf {self.host_path} {self.host_path_returned}")
            session = self._get_session(self.params, None)
            self._open_session_list.append(session)
            cmd_del_file = f"rm -rf {self.guest_path}"
            if self.params.get("os_type") == "windows":
                cmd_del_file = rf"del /f /q {self.guest_path}"
            session.cmd(cmd_del_file)


def run(test, params, env):
    """
    Freeze guest + create live snapshot + thaw guest

    Test steps:
    1) Create a big file inside on host.
    2) Scp the file from host to guest.
    3) Freeze guest during file transfer.
    4) Create live snapshot.
    5) Thaw guest.
    6) Scp the file from guest to host.
    7) Compare hash of those 2 files.

    :param test: kvm test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environmen.
    """
    gagent_test = QemuGuestAgentSnapshotTest(test, params, env)
    gagent_test.execute(test, params, env)
