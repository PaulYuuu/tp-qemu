import logging

from avocado.core import exceptions
from avocado.utils import process

LOG_JOB = logging.getLogger("avocado.test")


def create_volume(volume):
    if volume.preallocation == "full":
        if volume.pool.available < volume.capacity:
            raise exceptions.TestError(
                f"No enough free space, request '{volume.capacity}' but available in {volume.pool.name} is '{volume.pool.available}'"
            )
    else:
        if volume.format == "qcow2":
            if volume.pool.available * 1.2 < volume.capacity:
                raise exceptions.TestError(
                    f"No enough free space, request '{volume.capacity}' but available in {volume.pool.name} is '{volume.pool.available}'"
                )
    options = volume.generate_qemu_img_options()
    cmd = f"qemu-img create {options} {volume.key} {volume.capacity}B"
    LOG_JOB.debug("create volume cmd: %s", cmd)
    process.system(cmd, shell=True, ignore_status=False)
