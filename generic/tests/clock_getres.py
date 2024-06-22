import os

from virttest import data_dir, error_context, utils_test


@error_context.context_aware
def run(test, params, env):
    """
    Verify if guests using kvm-clock as the time source have a sane clock
    resolution.

    :param test: kvm test object.
    :param params: Dictionary with test parameters.
    :param env: Dictionary with the test environment.
    """

    vm = env.get_vm(params["main_vm"])
    vm.verify_alive()
    timeout = int(params.get("login_timeout", 360))
    session = vm.wait_for_login(timeout=timeout)

    getres_cmd = params.get("getres_cmd")

    if not getres_cmd or session.cmd_status(f"test -x {getres_cmd}"):
        source_name = "clock_getres/clock_getres.c"
        source_name = os.path.join(data_dir.get_deps_dir(), source_name)
        getres_cmd = "/tmp/clock_getres"
        dest_name = "/tmp/clock_getres.c"

        if not os.path.isfile(source_name):
            test.error(f"Could not find {source_name}")

        vm.copy_files_to(source_name, dest_name)
        session.cmd(f"gcc -lrt -o {getres_cmd} {dest_name}")

    session.cmd(getres_cmd)
    test.log.info("PASS: Guest reported appropriate clock resolution")
    sub_test = params.get("sub_test")
    if sub_test:
        error_context.context(
            f"Run sub test '{sub_test}' after checking" " clock resolution",
            test.log.info,
        )
        utils_test.run_virt_sub_test(test, params, env, sub_test)
