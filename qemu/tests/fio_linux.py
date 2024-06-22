"""
fio_linux.py include following case:
    1. Boot guest with "aio=native" or "aio=threads" CLI option and run fio
       tools.
"""

import re

from virttest import error_context, utils_misc

from provider.storage_benchmark import generate_instance


@error_context.context_aware
def run(test, params, env):
    """
    Boot guest with "aio=native" or "aio=threads" CLI option and run fio tools.
    Step:
        1. Boot guest with "aio=threads,cache=none" option.
        2. Run fio tool to the data disk in guest.
        3. Boot guest with "aio=native, cache=none" option
           (cmd lines refer to step 1).
        4. Run fio tool to the data disk in guest.

    :param test: QEMU test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def _get_data_disks():
        """Get the data disks by serial or wwn options."""
        for data_image in data_images:
            extra_params = params.get(f"blk_extra_params_{data_image}", "")
            match = re.search(r"(serial|wwn)=(\w+)", extra_params, re.M)
            if match:
                drive_id = match.group(2)
            else:
                continue
            drive_path = utils_misc.get_linux_drive_path(session, drive_id)
            if not drive_path:
                test.error(f"Failed to get '{data_image}' drive path")
            yield drive_path[5:]

    def _run_fio_test(target):
        for option in params["fio_options"].split(";"):
            fio.run(f"--filename={target} {option}")

    data_images = params["images"].split()[1:]
    info = []
    for image in data_images:
        aio = params.get(f"image_aio_{image}", "threads")
        cache = params.get(f"drive_cache_{image}", "none")
        info.append(f'{image}("aio={aio},cache={cache}")')
    test.log.info("Boot a guest with %s.", ", ".join(info))

    vm = env.get_vm(params["main_vm"])
    vm.verify_alive()
    session = vm.wait_for_login(timeout=float(params.get("login_timeout", 240)))
    fio = generate_instance(params, vm, "fio")
    try:
        if params.get("image_backend") == "nvme_direct":
            _run_fio_test(params.get("fio_filename"))
        else:
            for did in _get_data_disks():
                _run_fio_test(did)
    finally:
        fio.clean()
        session.close()
