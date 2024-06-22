import re

from avocado.utils import process
from virttest import env_process, error_context

try:
    cmp
except NameError:

    def cmp(x, y):
        return (x > y) - (x < y)


@error_context.context_aware
def run(test, params, env):
    """
    Check smbios table :
    1) Run the qemu command as nobody
    2) check the process is same as the user's

    :param test: QEMU test object.
    :param params: Dictionary with the test parameters.
    :param env: Dictionary with test environment.
    """

    def get_user_ugid(username):
        """
        return user uid and gid as a list
        """
        user_uid = process.getoutput(f"id -u {username}").split()
        user_gid = process.getoutput(f"id -g {username}").split()
        return user_uid, user_gid

    def get_ugid_from_processid(pid):
        """
        return a list[uid,euid,suid,fsuid,gid,egid,sgid,fsgid] of pid
        """
        grep_ugid_cmd = "cat /proc/%s/status | grep -iE '^(U|G)id'"
        o = process.getoutput(grep_ugid_cmd % pid, shell=True)
        ugid = re.findall(r"(\d+)", o)
        # real UID, effective UID, saved set UID, and file system UID
        if ugid:
            return ugid
        else:
            test.error(f"Could not find the correct UID for process {pid}")

    exec_username = params.get("user_runas", "nobody")

    error_context.base_context(f"Run QEMU {exec_username} test:")
    error_context.context("Get the user uid and gid,using 'id -u/g username'")
    (exec_uid, exec_gid) = get_user_ugid(exec_username)

    error_context.context(f"Run the qemu as user '{exec_username}'")
    test.log.info("The user %s :uid='%s', gid='%s'", exec_username, exec_uid, exec_gid)

    params["extra_params"] = f" -runas {exec_username}"
    params["start_vm"] = "yes"
    env_process.preprocess_vm(test, params, env, params.get("main_vm"))
    vm = env.get_vm(params["main_vm"])

    failures = []
    for pid in process.get_children_pids(vm.get_shell_pid()):
        error_context.context(
            f"Get the process '{pid}' u/gid, using 'cat " f"/proc/{pid}/status'",
            test.log.info,
        )
        qemu_ugid = get_ugid_from_processid(pid)
        test.log.info(
            "Process run as uid=%s,euid=%s,suid=%s,fsuid=%s", *tuple(qemu_ugid[0:4])
        )
        test.log.info(
            "Process run as gid=%s,egid=%s,sgid=%s,fsgid=%s", *tuple(qemu_ugid[4:])
        )

        error_context.context(
            f"Check if the user {exec_username} ugid is equal to the " f"process {pid}"
        )
        # generate user uid, euid, suid, fsuid, gid, egid, sgid, fsgid
        user_ugid_extend = exec_uid * 4 + exec_gid * 4
        if cmp(user_ugid_extend, qemu_ugid) != 0:
            e_msg = f"Process {pid} error, expect ugid is {user_ugid_extend}, real is {qemu_ugid}"
            failures.append(e_msg)

    if failures:
        test.fail(
            "FAIL: Test reported {} failures:\n{}".format(
                len(failures), "\n".join(failures)
            )
        )
