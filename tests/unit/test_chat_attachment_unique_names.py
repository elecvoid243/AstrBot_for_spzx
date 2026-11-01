from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image as PILImage
from starlette.datastructures import Headers, UploadFile

from astrbot.dashboard.api.multipart import UploadFileAdapter
from astrbot.dashboard.services.chat_service import (
    ChatService,
    unique_attachment_filename,
)


def _png(color):
    buffer = BytesIO()
    PILImage.new("RGB", (2, 2), color).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeUploadFile:
    content_type = "image/png"
    content_length = None

    def __init__(self, filename, data):
        self.filename = filename
        self.data = data

    async def save(self, destination, *, max_bytes=None):
        with open(destination, "wb") as output:
            output.write(self.data)


class FakeDatabase:
    def __init__(self):
        self.attachments = {}

    async def insert_attachment(self, path, type, mime_type):
        attachment_id = f"attachment-{len(self.attachments) + 1}"
        attachment = SimpleNamespace(attachment_id=attachment_id, path=path)
        self.attachments[attachment_id] = attachment
        return attachment

    async def get_attachments(self, attachment_ids, *, owner=None):
        return [self.attachments[i] for i in attachment_ids]

    async def delete_attachments(self, attachment_ids, *, owner=None):
        for attachment_id in attachment_ids:
            self.attachments.pop(attachment_id)


def make_service(tmp_path):
    service = ChatService.__new__(ChatService)
    service.db = FakeDatabase()
    service.attachments_dir = str(tmp_path)
    service.webchat_img_dir = str(tmp_path / "imgs")
    return service


@pytest.mark.asyncio
async def test_same_name_uploads_do_not_overwrite_each_other(tmp_path):
    service = make_service(tmp_path)
    red, blue = _png((255, 0, 0)), _png((0, 0, 255))

    first = await service.save_uploaded_file(FakeUploadFile("image.png", red))
    second = await service.save_uploaded_file(FakeUploadFile("image.png", blue))

    assert first["stored_filename"] != second["stored_filename"]
    assert (tmp_path / first["stored_filename"]).read_bytes() == red
    assert (tmp_path / second["stored_filename"]).read_bytes() == blue
    assert first["filename"] == second["filename"] == "image.png"


@pytest.mark.asyncio
async def test_deleting_an_attachment_keeps_same_name_files(tmp_path):
    service = make_service(tmp_path)
    first = await service.save_uploaded_file(
        FakeUploadFile("image.png", _png((1, 2, 3)))
    )
    second = await service.save_uploaded_file(
        FakeUploadFile("image.png", _png((4, 5, 6)))
    )

    await service.delete_attachments([second["attachment_id"]])

    assert (tmp_path / first["stored_filename"]).exists()
    assert not (tmp_path / second["stored_filename"]).exists()


def test_unique_attachment_filename_keeps_original_name():
    assert unique_attachment_filename("报告.pdf").endswith("_报告.pdf")


def test_unique_attachment_filename_fits_in_255_bytes():
    name = unique_attachment_filename("图" * 120 + ".png")

    assert len(name.encode()) <= 255
    assert name.endswith(".png")


def test_unique_attachment_filename_with_long_suffix_fits_in_255_bytes():
    name = unique_attachment_filename("v1." + "x" * 250)

    assert len(name.encode()) <= 255


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["a" * 300 + ".x", "图" * 120 + ".png"])
async def test_long_names_save_through_real_upload_adapter(tmp_path, filename):
    service = make_service(tmp_path)
    content = _png((7, 8, 9))
    upload = UploadFile(
        file=BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": "image/png"}),
    )

    result = await service.save_uploaded_file(UploadFileAdapter(upload))

    assert len(result["stored_filename"].encode()) <= 255
    assert result["filename"].endswith(".png")
    assert (tmp_path / result["stored_filename"]).read_bytes() == content
    assert not list(tmp_path.glob("*.tmp"))
