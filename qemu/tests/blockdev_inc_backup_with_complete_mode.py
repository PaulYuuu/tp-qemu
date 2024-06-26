from provider.blockdev_live_backup_base import BlockdevLiveBackupBaseTest
from provider.job_utils import get_event_by_condition


class BlockdevIncbkIncWithCompleteModeNeg(BlockdevLiveBackupBaseTest):
    """
    Do incremental backup to an image without enough space with setting:
    completion-mode: grouped/default
    """

    def __init__(self, test, params, env):
        super().__init__(test, params, env)
        self._inc_bk_nodes = [f"drive_{t}" for t in self._target_images]
        self._job_ids = [f"job_{n}" for n in self._inc_bk_nodes]

    def do_incremental_backup(self):
        job_list = [
            {
                "type": "blockdev-backup",
                "data": {
                    "device": self._source_nodes[i],
                    "target": self._inc_bk_nodes[i],
                    "sync": "incremental",
                    "job-id": self._job_ids[i],
                    "bitmap": self._bitmaps[i],
                },
            }
            for i, _ in enumerate(self._source_nodes)
        ]
        arguments = {"actions": job_list}
        if self.params.get("completion_mode"):
            arguments["properties"] = {
                "completion-mode": self.params["completion_mode"]
            }
        self.main_vm.monitor.cmd("transaction", arguments)

    def check_first_job_status(self):
        """
        when completion-mode is
            grouped: the 1st job should be cancelled
            default: the 1st job should complete without any error
        """
        job_event = (
            "BLOCK_JOB_CANCELLED"
            if self.params.get("completion_mode") == "grouped"
            else "BLOCK_JOB_COMPLETED"
        )
        tmo = self.params.get_numeric("job_completed_timeout", 360)
        cond = {"device": self._job_ids[0]}
        event = get_event_by_condition(self.main_vm, job_event, tmo, **cond)

        if event:
            if event["data"].get("error"):
                self.test.fail("Unexpected error: {}".format(event["data"]["error"]))
        else:
            self.test.fail(f"Failed to get {job_event} for the first job")

    def check_second_job_no_space_error(self):
        """
        We always get the 'no enough space' error for the 2nd job
        """
        tmo = self.params.get_numeric("job_completed_timeout", 360)
        cond = {"device": self._job_ids[1]}
        event = get_event_by_condition(self.main_vm, "BLOCK_JOB_COMPLETED", tmo, **cond)
        if event:
            if event["data"].get("error") != self.params["error_msg"]:
                self.test.fail("Unexpected error: {}".format(event["data"]["error"]))
        else:
            self.test.fail("Failed to get BLOCK_JOB_COMPLETED event")

    def do_test(self):
        self.do_full_backup()
        self.generate_inc_files()
        self.do_incremental_backup()
        self.check_first_job_status()
        self.check_second_job_no_space_error()


def run(test, params, env):
    """
    Do incremental backup to an image without enough space with setting:
    completion-mode: grouped/default

    test steps:
        1. boot VM with two 2G data disks
        2. format data disks and mount both, create files
        3. add 2 2G target disks for full backup,
           add a 2G target disk for inc backup,
           add a 2G(actual size<2G) target disk for inc backup
        4. do full backup and add bitmap
        5. create another files (size 110M)
        6. do inc backup with compeletion-mode
           compeletion-mode=default, the 1st inc backup job completed
           compeletion-mode=grouped, the 1st inc backup job cancelled
           The 2nd inc backup job always failed due to 'no space left' error

    :param test: test object
    :param params: test configuration dict
    :param env: env object
    """
    inc_test = BlockdevIncbkIncWithCompleteModeNeg(test, params, env)
    inc_test.run_test()
