import os

from virttest import data_dir, error_context


@error_context.context_aware
def run(test, params, env):
    """
    Check kvm 0x40000001 inside guest.
    :param test: QEMU test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    vm = env.get_vm(params["main_vm"])
    session = vm.wait_for_login()
    nums_cpu = str(vm.cpuinfo.smp)

    test_dir = params["test_dir"]
    source_file = params["source_file"]
    src_cpuid = os.path.join(data_dir.get_deps_dir(), source_file)
    vm.copy_files_to(src_cpuid, test_dir)
    guest_dir = f"{test_dir}/cpuid-20220224"
    try:
        session.cmd(f"tar -xvf {test_dir}/{source_file} -C {test_dir}")
        check_cpuid = f"cd {guest_dir} && " + params["check_cpuid"]
        results = session.cmd_output(check_cpuid).strip()
        if results.split()[0] != nums_cpu:
            test.fail("some vcpu's cpuid has no eax=0x40000001.")
    finally:
        session.cmd(f"rm {test_dir}/cpuid* -rf")
        session.close()
