"""Transparent author protocol gates with synthetic pixels/native bytes only."""
import copy
import hashlib
import io
import json
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image
import test_s1_author_package as fixture
import s1_author_package as s1
import star_map_import as api


class TransparentAuthorPackageTests(unittest.TestCase):
    def setUp(self):
        self.fx=fixture.S1AuthorPackageTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.approved=set()
        self.enterContext(patch.object(s1,'APPROVED_TRANSPARENT_BINDINGS',self.approved))
        self.fx.write('_internal/version.json',fixture.encoded(dict(displayVersion='1.1.0',numericVersion='1.0.0.22')))

    def archive(self, *, approved=True, mode='RGBA', package_id=s1.TRANSPARENT_PROFILE_ID):
        doc=copy.deepcopy(self.fx.document)
        doc.update(profileId=s1.TRANSPARENT_PROFILE_ID,authorName='爱吃醋')
        image=Image.new(mode,(16,16),(0,255,0,0) if mode=='RGBA' else (0,255,0))
        image.putpixel((8,8),(0,255,0,255) if mode=='RGBA' else (0,255,0))
        stream=io.BytesIO();image.save(stream,format='PNG');pixels=stream.getvalue()
        payload={}
        for row in doc['layers']:
            path=s1.image_path(row['mode'],row['mapId'],row['floor'],s1.TRANSPARENT_PROFILE_ID)
            row.update(displayPath=path,displaySha256=hashlib.sha256(pixels).hexdigest(),displayBytes=len(pixels))
            payload[path]=pixels
        raw=fixture.encoded(doc)
        payload[s1.bindings_path(s1.TRANSPARENT_PROFILE_ID)]=raw
        if approved:self.approved.add(hashlib.sha256(raw).hexdigest())
        meta=dict(schema=s1.SCHEMA,schemaVersion=1,revision=1,season='s1',sourceVersion=s1.SOURCE_VERSION,
            packageId=package_id,clientCompatibility=api.CLIENT_COMPATIBILITY,
            files=[fixture.record(p,b) for p,b in payload.items()],requiresFiles=self.fx.dependencies,
            approved=True)
        target=self.fx.base/'transparent.zip'
        with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('map-package.json',fixture.encoded(meta))
            for path,data in payload.items():z.writestr('data/'+path,data)
        return target

    def test_1_1_0_import_is_separate_from_original_receipt_and_noop_preserves_it(self):
        self.fx.install()
        old=self.fx.root/f'work/map-packages/{s1.PROFILE_ID}.json'
        before=old.read_bytes(),old.stat().st_ino
        incoming=self.archive()
        result=self.fx.install(incoming)
        self.assertEqual((result['packageId'],result['added']),(s1.TRANSPARENT_PROFILE_ID,88))
        new=self.fx.root/f'work/map-packages/{s1.TRANSPARENT_PROFILE_ID}.json'
        receipt=new.read_bytes(),new.stat().st_ino
        self.assertTrue(self.fx.install(incoming)['receiptPreserved'])
        self.assertEqual((new.read_bytes(),new.stat().st_ino),receipt)
        self.assertEqual((old.read_bytes(),old.stat().st_ino),before)
        self.fx.assert_untouched()

    def test_archive_self_approval_does_not_replace_source_registration(self):
        with self.assertRaisesRegex(api.ImportRejected,'s1-author-registration-not-reviewed'):
            self.fx.inspect(self.archive(approved=False))
        self.assertFalse((self.fx.root/f'work/map-packages/{s1.TRANSPARENT_PROFILE_ID}.json').exists())
        self.fx.assert_untouched()

    def test_transparent_paths_cannot_be_claimed_by_old_package_id(self):
        with self.assertRaises(api.ImportRejected):
            self.fx.inspect(self.archive(package_id=s1.PROFILE_ID))

    def test_transparent_profile_requires_rgba_even_with_reviewed_registration(self):
        with self.assertRaisesRegex(api.ImportRejected,'s1-author-invalid-image'):
            self.fx.inspect(self.archive(mode='RGB'))

    def test_reviewed_transparent_registration_still_requires_exact_native_bytes(self):
        incoming=self.archive()
        target=self.fx.root/self.fx.dependencies[0]['path']
        target.write_bytes(b'changed-native')
        with self.assertRaises(api.ImportRejected):
            self.fx.inspect(incoming)
