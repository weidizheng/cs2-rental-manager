import os
import tempfile
import time
import unittest
from unittest.mock import patch

from modules.image_cache import ImageCache


class ImageCacheTests(unittest.TestCase):
    def test_prune_removes_the_oldest_regenerable_file_first(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "modules.image_cache.IMAGES_DIR", directory
        ):
            old_path = os.path.join(directory, "old.img")
            new_path = os.path.join(directory, "new.img")
            with open(old_path, "wb") as file:
                file.write(b"old")
            with open(new_path, "wb") as file:
                file.write(b"new")
            os.utime(old_path, (time.time() - 60, time.time() - 60))

            ImageCache.prune(max_bytes=10, max_files=1)

            self.assertFalse(os.path.exists(old_path))
            self.assertTrue(os.path.exists(new_path))
