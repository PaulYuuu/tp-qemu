from avocado.utils import process


def run(test, params, env):
    """
    run perf tool to get kvm events info

    :param test: kvm test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    vm = env.get_vm(params["main_vm"])
    vm.verify_alive()

    login_timeout = int(params.get("login_timeout", 360))
    transfer_timeout = int(params.get("transfer_timeout", 240))
    perf_record_timeout = int(params.get("perf_record_timeout", 240))
    vm_kallsyms_path = "/tmp/guest_kallsyms"
    vm_modules_path = "/tmp/guest_modules"

    # Prepare test environment in guest
    session = vm.wait_for_login(timeout=login_timeout)

    session.cmd(f"cat /proc/kallsyms > {vm_kallsyms_path}")
    session.cmd(f"cat /proc/modules > {vm_modules_path}")

    vm.copy_files_from("/tmp/guest_kallsyms", "/tmp", timeout=transfer_timeout)
    vm.copy_files_from("/tmp/guest_modules", "/tmp", timeout=transfer_timeout)

    perf_record_cmd = f"perf kvm --host --guest --guestkallsyms={vm_kallsyms_path}"
    perf_record_cmd += f" --guestmodules={vm_modules_path} record -a -o /tmp/perf.data sleep {perf_record_timeout} "
    perf_report_cmd = f"perf kvm --host --guest --guestkallsyms={vm_kallsyms_path}"
    perf_report_cmd += (
        f" --guestmodules={vm_modules_path} report -i /tmp/perf.data --force "
    )

    process.system(perf_record_cmd)
    process.system(perf_report_cmd)

    session.close()
