class UnsupportedStoragePoolException(Exception):
    def __init__(self, sp_manager, sp_type):
        self.sp_manager = sp_manager
        self.sp_type = sp_type
        self.message = f"Unsupported StoragePool type '{self.sp_type}', supported type are: {sp_manager.supported_storage_backend.keys()}"

    def __str__(self):
        return f"UnsupportedStoragePoolException:{self.message}"
