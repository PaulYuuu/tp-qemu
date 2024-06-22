import os
import re

from avocado.utils import process
from virttest import error_context, funcatexit


def cleanup(debugfs_path, session):
    """
    Umount the debugfs and close the session
    """
    if os.path.ismount(debugfs_path):
        process.run(f"umount {debugfs_path}", shell=True)
    if os.path.isdir(debugfs_path):
        os.removedirs(debugfs_path)
    session.close()


@error_context.context_aware
def run(test, params, env):
    """
    KVM kernel hugepages user side test:
    1) Smoke test
    2) Stress test

    :param test: QEMU test object.
    :param params: Dictionary with test parameters.
    :param env: Dictionary with the test environment.
    """

    def get_mem_status(params, role):
        if role == "host":
            info = process.getoutput("cat /proc/meminfo")
        else:
            info = session.cmd("cat /proc/meminfo")
        output = None
        for h in re.split("\n+", info):
            if h.startswith(f"{params}"):
                output = re.split(r"\s+", h)[1]
        if output is None:
            raise ValueError(f"unsupported meminfo param: {params}")
        return int(output)

    dd_timeout = float(params.get("dd_timeout", 900))
    mem = params.get_numeric("mem")
    largepages_files = params.objects("largepages_files")
    failures = []

    debugfs_flag = 1
    debugfs_path = os.path.join(test.tmpdir, "debugfs")
    mem_path = os.path.join("/tmp", "thp_space")

    login_timeout = float(params.get("login_timeout", "3600"))

    error_context.context("smoke test setup")
    if not os.path.ismount(debugfs_path):
        if not os.path.isdir(debugfs_path):
            os.makedirs(debugfs_path)
        process.run(f"mount -t debugfs none {debugfs_path}", shell=True)

    vm = env.get_vm(params.get("main_vm"))
    session = vm.wait_for_login(timeout=login_timeout)

    funcatexit.register(env, params.get("type"), cleanup, debugfs_path, session)

    test.log.info("Smoke test start")
    error_context.context("smoke test")

    nr_ah_before = get_mem_status("AnonHugePages", "host")
    if nr_ah_before <= 0:
        e_msg = "smoke: Host is not using THP"
        test.log.error(e_msg)
        failures.append(e_msg)

    # Protect system from oom killer
    if get_mem_status("MemFree", "guest") // 1024 < mem:
        mem = get_mem_status("MemFree", "guest") // 1024

    session.cmd(f"mkdir -p {mem_path}")

    session.cmd(f"mount -t tmpfs -o size={str(mem)}M none {mem_path}")

    count = mem // 4
    session.cmd(
        f"dd if=/dev/zero of={mem_path}/1 bs=4000000 count={count}",
        timeout=dd_timeout,
    )

    nr_ah_after = get_mem_status("AnonHugePages", "host")

    if nr_ah_after <= nr_ah_before:
        e_msg = "smoke: Host did not use new THP during dd"
        test.log.error(e_msg)
        failures.append(e_msg)

    if debugfs_flag == 1:
        largepages = 0
        for largepages_file in largepages_files:
            largepages_path = f"{debugfs_path}/kvm/{largepages_file}"
            if os.path.exists(largepages_path):
                largepages += int(open(largepages_path).read())

        if largepages <= 0:
            e_msg = "smoke: KVM is not using THP"
            test.log.error(e_msg)
            failures.append(e_msg)

    test.log.info("Smoke test finished")

    # Use parallel dd as stress for memory
    count = count // 3
    test.log.info("Stress test start")
    error_context.context("stress test")
    cmd = f"rm -rf {mem_path}/*; for i in `seq {count}`; do dd "
    cmd += f"if=/dev/zero of={mem_path}/$i bs=4000000 count=1& done;wait"
    output = session.cmd_output(cmd, timeout=dd_timeout)

    if len(re.findall("No space", output)) > count * 0.05:
        e_msg = "stress: Too many dd instances failed in guest"
        test.log.error(e_msg)
        failures.append(e_msg)

    try:
        output = session.cmd("pidof dd")
    except Exception:
        output = None

    if output is not None:
        for i in re.split("\n+", output):
            session.cmd(f"kill -9 {i}")

    session.cmd(f"umount {mem_path}")

    test.log.info("Stress test finished")

    error_context.context("")
    if failures:
        test.fail(
            "THP base test reported {} failures:\n{}".format(
                len(failures), "\n".join(failures)
            )
        )
