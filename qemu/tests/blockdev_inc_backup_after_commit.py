from virttest.qemu_devices.qdevices import QBlockdevFormatNode

from provider import backup_utils, block_dirty_bitmap, blockdev_base


class BlockdevIncbkAfterCommitTest(blockdev_base.BlockdevBaseTest):
    def __init__(self, test, params, env):
        super().__init__(test, params, env)
        self._source_nodes = []
        self._full_bk_nodes = []
        self._inc_bk_nodes = []
        self._inc_bk_images = []
        self._bitmaps = []
        self._snap_images = []
        self._snap_nodes = []
        self._source_images = params.objects("source_images")
        list(map(self._init_arguments_by_params, self._source_images))

    def _init_arguments_by_params(self, tag):
        image_params = self.params.object_params(tag)
        image_chain = image_params.objects("image_backup_chain")
        self._source_nodes.append(f"drive_{tag}")
        self._full_bk_nodes.append(f"drive_{image_chain[0]}")
        self._inc_bk_nodes.append(f"drive_{image_chain[1]}")
        self._inc_bk_images.append(image_chain[1])
        self._snap_images.append(image_params["snap_image"])
        self._snap_nodes.append(f"drive_{self._snap_images[-1]}")
        self._bitmaps.append(f"bitmap_{tag}")

        # Add the full backup image only before full backup
        self.params[f"image_backup_chain_{tag}"] = image_chain[0]

    def add_images_for_incremental_backup(self):
        """add incremental backup images with qmp command"""
        for idx, tag in enumerate(self._source_images):
            self.params[f"image_backup_chain_{tag}"] = self._inc_bk_images[idx]
        self.add_target_data_disks()

    def add_images_for_data_image_snapshots(self):
        """add snapshot images with backing:null"""
        for tag in self._snap_images:
            # create image with qemu-img
            disk = self.source_disk_define_by_params(self.params, tag)
            disk.create(self.params)
            self.trash.append(disk)

            # hotplug image with blockdev-add(format and protocol only)
            params = self.params.object_params(tag)
            devices = self.main_vm.devices.images_define_by_params(tag, params, "disk")
            devices.pop()
            for dev in devices:
                if self.main_vm.devices.get_by_qid(dev.get_qid()):
                    continue
                if isinstance(dev, QBlockdevFormatNode):
                    dev.params["backing"] = None
                ret = self.main_vm.devices.simple_hotplug(dev, self.main_vm.monitor)
                if not ret[1]:
                    self.test.fail(f"Failed to hotplug '{dev}': {ret[0]}.")

    def do_full_backup(self):
        """full backup: data->base"""
        extra_options = {"sync": "full"}
        backup_utils.blockdev_batch_backup(
            self.main_vm,
            self._source_nodes,
            self._full_bk_nodes,
            self._bitmaps,
            **extra_options,
        )

    def generate_new_files(self):
        return list(map(self.generate_data_file, self._source_images))

    def do_incremental_backup(self):
        """incremental backup: data->inc"""
        extra_options = {"sync": "incremental"}
        backup_utils.blockdev_batch_backup(
            self.main_vm,
            self._source_nodes,
            self._inc_bk_nodes,
            self._bitmaps,
            **extra_options,
        )

    def clone_vm_with_incremental_images(self):
        """clone VM with incremental backup images as vm's data images"""
        if self.main_vm.is_alive():
            self.main_vm.destroy()

        params = self.main_vm.params.copy()
        images = [params.objects("images")[0]] + self._inc_bk_images
        params["images"] = " ".join(images)

        self.clone_vm = self.main_vm.clone(params=params)
        self.clone_vm.create()
        self.clone_vm.verify_alive()

        self.env.register_vm(f"{self.clone_vm.name}_clone", self.clone_vm)

    def take_snapshots_on_data_images(self):
        """take snapshots on data images"""
        snapshot_options = {}
        for idx, source_node in enumerate(self._source_nodes):
            backup_utils.blockdev_snapshot(
                self.main_vm, source_node, self._snap_nodes[idx], **snapshot_options
            )

    def commit_snapshots_on_data_images(self):
        """commit snapshots onto data images"""
        commit_options = {}
        for idx, snap_node in enumerate(self._snap_nodes):
            backup_utils.block_commit(self.main_vm, snap_node, **commit_options)

    def check_bitmaps(self):
        for idx, bitmap in enumerate(self._bitmaps):
            info = block_dirty_bitmap.get_bitmap_by_name(
                self.main_vm, self._source_nodes[idx], bitmap
            )
            if info:
                if info["count"] <= 0:
                    self.test.fail("count in bitmap must be greater than 0")
            else:
                self.test.fail(f"Failed to find bitmap {bitmap}")

    def do_test(self):
        self.do_full_backup()
        self.generate_new_files()
        self.add_images_for_data_image_snapshots()
        self.take_snapshots_on_data_images()
        self.generate_new_files()
        self.commit_snapshots_on_data_images()
        self.check_bitmaps()
        self.add_images_for_incremental_backup()
        self.do_incremental_backup()
        self.clone_vm_with_incremental_images()
        self.verify_data_files()


def run(test, params, env):
    """
    Do incremental backup after block commit

    test steps:
        1. boot VM with a 2G data disk
        2. format data disk and mount it, create a file
        3. hotplug an image for full backup
        4. do full backup(data->base) and add non-persistent bitmap
        5. create another file
        6. add an image(backing:null) for data image snapshot
        7. create another file
        8. commit snapshot image on data image
        9. hotplug an image(backing:snapshot image) for incremental backup
        9. do incremental backup(data->inc)
       10. clone VM with inc image as its data image
       11. check files and md5sum

    :param test: test object
    :param params: test configuration dict
    :param env: env object
    """
    inc_test = BlockdevIncbkAfterCommitTest(test, params, env)
    inc_test.run_test()
