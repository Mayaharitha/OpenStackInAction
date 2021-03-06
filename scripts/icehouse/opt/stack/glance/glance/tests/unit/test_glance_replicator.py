#    Copyright 2012 Michael Still and Canonical Inc
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import copy
import os
import sys
import UserDict
import uuid

import fixtures
import six

from glance.cmd import replicator as glance_replicator
from glance.openstack.common import jsonutils
from glance.tests.unit import utils as unit_test_utils
from glance.tests import utils as test_utils


IMG_RESPONSE_ACTIVE = {
    'content-length': '0',
    'property-image_state': 'available',
    'min_ram': '0',
    'disk_format': 'aki',
    'updated_at': '2012-06-25T02:10:36',
    'date': 'Thu, 28 Jun 2012 07:20:05 GMT',
    'owner': '8aef75b5c0074a59aa99188fdb4b9e90',
    'id': '6d55dd55-053a-4765-b7bc-b30df0ea3861',
    'size': '4660272',
    'property-image_location': 'ubuntu-bucket/oneiric-server-cloudimg-amd64-'
                               'vmlinuz-generic.manifest.xml',
    'property-architecture': 'x86_64',
    'etag': 'f46cfe7fb3acaff49a3567031b9b53bb',
    'location': 'http://127.0.0.1:9292/v1/images/'
                '6d55dd55-053a-4765-b7bc-b30df0ea3861',
    'container_format': 'aki',
    'status': 'active',
    'deleted': 'False',
    'min_disk': '0',
    'is_public': 'False',
    'name': 'ubuntu-bucket/oneiric-server-cloudimg-amd64-vmlinuz-generic',
    'checksum': 'f46cfe7fb3acaff49a3567031b9b53bb',
    'created_at': '2012-06-25T02:10:32',
    'protected': 'False',
    'content-type': 'text/html; charset=UTF-8'
}

IMG_RESPONSE_QUEUED = copy.copy(IMG_RESPONSE_ACTIVE)
IMG_RESPONSE_QUEUED['status'] = 'queued'
IMG_RESPONSE_QUEUED['id'] = '49b2c782-ee10-4692-84f8-3942e9432c4b'
IMG_RESPONSE_QUEUED['location'] = ('http://127.0.0.1:9292/v1/images/'
                                   + IMG_RESPONSE_QUEUED['id'])


class FakeHTTPConnection(object):
    def __init__(self):
        self.count = 0
        self.reqs = {}
        self.last_req = None
        self.host = 'localhost'
        self.port = 9292

    def prime_request(self, method, url, in_body, in_headers,
                      out_code, out_body, out_headers):
        if not url.startswith('/'):
            url = '/' + url
        url = unit_test_utils.sort_url_by_qs_keys(url)
        hkeys = in_headers.keys()
        hkeys.sort()
        hashable = (method, url, in_body, ' '.join(hkeys))

        flat_headers = []
        for key in out_headers:
            flat_headers.append((key, out_headers[key]))

        self.reqs[hashable] = (out_code, out_body, flat_headers)

    def request(self, method, url, body, headers):
        self.count += 1
        url = unit_test_utils.sort_url_by_qs_keys(url)
        hkeys = headers.keys()
        hkeys.sort()
        hashable = (method, url, body, ' '.join(hkeys))

        if hashable not in self.reqs:
            options = []
            for h in self.reqs:
                options.append(repr(h))

            raise Exception('No such primed request: %s "%s"\n'
                            '%s\n\n'
                            'Available:\n'
                            '%s'
                            % (method, url, hashable, '\n\n'.join(options)))
        self.last_req = hashable

    def getresponse(self):
        class FakeResponse(object):
            def __init__(self, (code, body, headers)):
                self.body = six.StringIO(body)
                self.headers = headers
                self.status = code

            def read(self, count=1000000):
                return self.body.read(count)

            def getheaders(self):
                return self.headers

        return FakeResponse(self.reqs[self.last_req])


class ImageServiceTestCase(test_utils.BaseTestCase):
    def test_rest_errors(self):
        c = glance_replicator.ImageService(FakeHTTPConnection(), 'noauth')

        for code, exc in [(400, glance_replicator.ServerErrorException),
                          (401, glance_replicator.AuthenticationException),
                          (403, glance_replicator.AuthenticationException),
                          (409,
                           glance_replicator.ImageAlreadyPresentException),
                          (500, glance_replicator.ServerErrorException)]:
            c.conn.prime_request('GET',
                                 ('v1/images/'
                                  '5dcddce0-cba5-4f18-9cf4-9853c7b207a6'), '',
                                 {'x-auth-token': 'noauth'}, code, '', {})
            self.assertRaises(exc, c.get_image,
                              '5dcddce0-cba5-4f18-9cf4-9853c7b207a6')

    def test_rest_get_images(self):
        c = glance_replicator.ImageService(FakeHTTPConnection(), 'noauth')

        # Two images, one of which is queued
        resp = {'images': [IMG_RESPONSE_ACTIVE, IMG_RESPONSE_QUEUED]}
        c.conn.prime_request('GET', 'v1/images/detail?is_public=None', '',
                             {'x-auth-token': 'noauth'},
                             200, jsonutils.dumps(resp), {})
        c.conn.prime_request('GET',
                             ('v1/images/detail?marker=%s&is_public=None'
                              % IMG_RESPONSE_QUEUED['id']),
                             '', {'x-auth-token': 'noauth'},
                             200, jsonutils.dumps({'images': []}), {})

        imgs = list(c.get_images())
        self.assertEqual(len(imgs), 2)
        self.assertEqual(c.conn.count, 2)

    def test_rest_get_image(self):
        c = glance_replicator.ImageService(FakeHTTPConnection(), 'noauth')

        image_contents = 'THISISTHEIMAGEBODY'
        c.conn.prime_request('GET',
                             'v1/images/%s' % IMG_RESPONSE_ACTIVE['id'],
                             '', {'x-auth-token': 'noauth'},
                             200, image_contents, IMG_RESPONSE_ACTIVE)

        body = c.get_image(IMG_RESPONSE_ACTIVE['id'])
        self.assertEqual(body.read(), image_contents)

    def test_rest_header_list_to_dict(self):
        i = [('x-image-meta-banana', 42),
             ('gerkin', 12),
             ('x-image-meta-property-frog', 11),
             ('x-image-meta-property-duck', 12)]
        o = glance_replicator.ImageService._header_list_to_dict(i)
        self.assertTrue('banana' in o)
        self.assertTrue('gerkin' in o)
        self.assertTrue('properties' in o)
        self.assertTrue('frog' in o['properties'])
        self.assertTrue('duck' in o['properties'])
        self.assertFalse('x-image-meta-banana' in o)

    def test_rest_get_image_meta(self):
        c = glance_replicator.ImageService(FakeHTTPConnection(), 'noauth')

        c.conn.prime_request('HEAD',
                             'v1/images/%s' % IMG_RESPONSE_ACTIVE['id'],
                             '', {'x-auth-token': 'noauth'},
                             200, '', IMG_RESPONSE_ACTIVE)

        header = c.get_image_meta(IMG_RESPONSE_ACTIVE['id'])
        self.assertTrue('id' in header)

    def test_rest_dict_to_headers(self):
        i = {'banana': 42,
             'gerkin': 12,
             'properties': {'frog': 1,
                            'kernel_id': None}
             }
        o = glance_replicator.ImageService._dict_to_headers(i)
        self.assertTrue('x-image-meta-banana' in o)
        self.assertTrue('x-image-meta-gerkin' in o)
        self.assertTrue('x-image-meta-property-frog' in o)
        self.assertTrue('x-image-meta-property-kernel_id' in o)
        self.assertEqual(o['x-image-meta-property-kernel_id'], '')
        self.assertFalse('properties' in o)

    def test_rest_add_image(self):
        c = glance_replicator.ImageService(FakeHTTPConnection(), 'noauth')

        image_body = 'THISISANIMAGEBODYFORSURE!'
        image_meta_with_proto = {}
        image_meta_with_proto['x-auth-token'] = 'noauth'
        image_meta_with_proto['Content-Type'] = 'application/octet-stream'
        image_meta_with_proto['Content-Length'] = len(image_body)

        for key in IMG_RESPONSE_ACTIVE:
            image_meta_with_proto['x-image-meta-%s' % key] = \
                IMG_RESPONSE_ACTIVE[key]

        c.conn.prime_request('POST', 'v1/images',
                             image_body, image_meta_with_proto,
                             200, '', IMG_RESPONSE_ACTIVE)

        headers, body = c.add_image(IMG_RESPONSE_ACTIVE, image_body)
        self.assertEqual(headers, IMG_RESPONSE_ACTIVE)
        self.assertEqual(c.conn.count, 1)

    def test_rest_add_image_meta(self):
        c = glance_replicator.ImageService(FakeHTTPConnection(), 'noauth')

        image_meta = {'id': '5dcddce0-cba5-4f18-9cf4-9853c7b207a6'}
        image_meta_headers = \
            glance_replicator.ImageService._dict_to_headers(image_meta)
        image_meta_headers['x-auth-token'] = 'noauth'
        image_meta_headers['Content-Type'] = 'application/octet-stream'
        c.conn.prime_request('PUT', 'v1/images/%s' % image_meta['id'],
                             '', image_meta_headers, 200, '', '')
        headers, body = c.add_image_meta(image_meta)


class FakeHttpResponse(object):
    def __init__(self, headers, data):
        self.headers = headers
        self.data = six.StringIO(data)

    def getheaders(self):
        return self.headers

    def read(self, amt=None):
        return self.data.read(amt)


FAKEIMAGES = [{'status': 'active', 'size': 100, 'dontrepl': 'banana',
               'id': '5dcddce0-cba5-4f18-9cf4-9853c7b207a6'},
              {'status': 'deleted', 'size': 200, 'dontrepl': 'banana',
               'id': 'f4da1d2a-40e8-4710-b3aa-0222a4cc887b'},
              {'status': 'active', 'size': 300, 'dontrepl': 'banana',
               'id': '37ff82db-afca-48c7-ae0b-ddc7cf83e3db'}]
FAKEIMAGES_LIVEMASTER = [{'status': 'active', 'size': 100,
                          'dontrepl': 'banana',
                          'id': '5dcddce0-cba5-4f18-9cf4-9853c7b207a6'},
                         {'status': 'deleted', 'size': 200,
                          'dontrepl': 'banana',
                          'id': 'f4da1d2a-40e8-4710-b3aa-0222a4cc887b'},
                         {'status': 'deleted', 'size': 300,
                          'dontrepl': 'banana',
                          'id': '37ff82db-afca-48c7-ae0b-ddc7cf83e3db'},
                         {'status': 'active', 'size': 100,
                          'dontrepl': 'banana',
                          'id': '15648dd7-8dd0-401c-bd51-550e1ba9a088'}]


class FakeImageService(object):
    def __init__(self, http_conn, authtoken):
        self.authtoken = authtoken

    def get_images(self):
        if self.authtoken == 'livemaintoken':
            return FAKEIMAGES_LIVEMASTER
        return FAKEIMAGES

    def get_image(self, id):
        return FakeHttpResponse({}, 'data')

    def get_image_meta(self, id):
        for img in FAKEIMAGES:
            if img['id'] == id:
                return img
        return {}

    def add_image_meta(self, meta):
        return {'status': 200}, None

    def add_image(self, meta, data):
        return {'status': 200}, None


def get_image_service():
    return FakeImageService


def check_no_args(command, args):
    options = UserDict.UserDict()
    no_args_error = False

    orig_img_service = glance_replicator.get_image_service
    try:
        glance_replicator.get_image_service = get_image_service
        command(options, args)
    except TypeError:
        no_args_error = True
    finally:
        glance_replicator.get_image_service = orig_img_service

    return no_args_error


def check_bad_args(command, args):
    options = UserDict.UserDict()
    bad_args_error = False

    orig_img_service = glance_replicator.get_image_service
    try:
        glance_replicator.get_image_service = get_image_service
        command(options, args)
    except ValueError:
        bad_args_error = True
    finally:
        glance_replicator.get_image_service = orig_img_service

    return bad_args_error


class ReplicationCommandsTestCase(test_utils.BaseTestCase):
    def test_replication_size(self):
        options = UserDict.UserDict()
        options.subordinatetoken = 'subordinatetoken'
        args = ['localhost:9292']

        stdout = sys.stdout
        orig_img_service = glance_replicator.get_image_service
        sys.stdout = six.StringIO()
        try:
            glance_replicator.get_image_service = get_image_service
            glance_replicator.replication_size(options, args)
            sys.stdout.seek(0)
            output = sys.stdout.read()
        finally:
            sys.stdout = stdout
            glance_replicator.get_image_service = orig_img_service

        output = output.rstrip()
        self.assertEqual(output, 'Total size is 400 bytes across 2 images')

    def test_replication_size_with_no_args(self):
        args = []
        command = glance_replicator.replication_size
        self.assertTrue(check_no_args(command, args))

    def test_replication_size_with_bad_args(self):
        args = ['aaa']
        command = glance_replicator.replication_size
        self.assertTrue(check_bad_args(command, args))

    def test_replication_dump(self):
        tempdir = self.useFixture(fixtures.TempDir()).path

        options = UserDict.UserDict()
        options.chunksize = 4096
        options.maintoken = 'maintoken'
        options.metaonly = False
        args = ['localhost:9292', tempdir]

        orig_img_service = glance_replicator.get_image_service
        self.addCleanup(setattr, glance_replicator,
                        'get_image_service', orig_img_service)
        glance_replicator.get_image_service = get_image_service
        glance_replicator.replication_dump(options, args)

        for active in ['5dcddce0-cba5-4f18-9cf4-9853c7b207a6',
                       '37ff82db-afca-48c7-ae0b-ddc7cf83e3db']:
            imgfile = os.path.join(tempdir, active)
            self.assertTrue(os.path.exists(imgfile))
            self.assertTrue(os.path.exists('%s.img' % imgfile))

            with open(imgfile) as f:
                d = jsonutils.loads(f.read())
                self.assertTrue('status' in d)
                self.assertTrue('id' in d)
                self.assertTrue('size' in d)

        for inactive in ['f4da1d2a-40e8-4710-b3aa-0222a4cc887b']:
            imgfile = os.path.join(tempdir, inactive)
            self.assertTrue(os.path.exists(imgfile))
            self.assertFalse(os.path.exists('%s.img' % imgfile))

            with open(imgfile) as f:
                d = jsonutils.loads(f.read())
                self.assertTrue('status' in d)
                self.assertTrue('id' in d)
                self.assertTrue('size' in d)

    def test_replication_dump_with_no_args(self):
        args = []
        command = glance_replicator.replication_dump
        self.assertTrue(check_no_args(command, args))

    def test_replication_dump_with_bad_args(self):
        args = ['aaa', 'bbb']
        command = glance_replicator.replication_dump
        self.assertTrue(check_bad_args(command, args))

    def test_replication_load(self):
        tempdir = self.useFixture(fixtures.TempDir()).path

        def write_image(img, data):
            imgfile = os.path.join(tempdir, img['id'])
            with open(imgfile, 'w') as f:
                f.write(jsonutils.dumps(img))

            if data:
                with open('%s.img' % imgfile, 'w') as f:
                    f.write(data)

        for img in FAKEIMAGES:
            cimg = copy.copy(img)
            # We need at least one image where the stashed metadata on disk
            # is newer than what the fake has
            if cimg['id'] == '5dcddce0-cba5-4f18-9cf4-9853c7b207a6':
                cimg['extra'] = 'thisissomeextra'

            # This is an image where the metadata change should be ignored
            if cimg['id'] == 'f4da1d2a-40e8-4710-b3aa-0222a4cc887b':
                cimg['dontrepl'] = 'thisisyetmoreextra'

            write_image(cimg, 'kjdhfkjshdfkjhsdkfd')

        # And an image which isn't on the destination at all
        new_id = str(uuid.uuid4())
        cimg['id'] = new_id
        write_image(cimg, 'dskjfhskjhfkfdhksjdhf')

        # And an image which isn't on the destination, but lacks image
        # data
        new_id_missing_data = str(uuid.uuid4())
        cimg['id'] = new_id_missing_data
        write_image(cimg, None)

        # A file which should be ignored
        badfile = os.path.join(tempdir, 'kjdfhf')
        with open(badfile, 'w') as f:
            f.write(jsonutils.dumps([1, 2, 3, 4, 5]))

        # Finally, we're ready to test
        options = UserDict.UserDict()
        options.dontreplicate = 'dontrepl dontreplabsent'
        options.subordinatetoken = 'subordinatetoken'
        args = ['localhost:9292', tempdir]

        orig_img_service = glance_replicator.get_image_service
        try:
            glance_replicator.get_image_service = get_image_service
            updated = glance_replicator.replication_load(options, args)
        finally:
            glance_replicator.get_image_service = orig_img_service

        self.assertTrue('5dcddce0-cba5-4f18-9cf4-9853c7b207a6' in updated)
        self.assertFalse('f4da1d2a-40e8-4710-b3aa-0222a4cc887b' in updated)
        self.assertTrue(new_id in updated)
        self.assertFalse(new_id_missing_data in updated)

    def test_replication_load_with_no_args(self):
        args = []
        command = glance_replicator.replication_load
        self.assertTrue(check_no_args(command, args))

    def test_replication_load_with_bad_args(self):
        args = ['aaa', 'bbb']
        command = glance_replicator.replication_load
        self.assertTrue(check_bad_args(command, args))

    def test_replication_livecopy(self):
        options = UserDict.UserDict()
        options.chunksize = 4096
        options.dontreplicate = 'dontrepl dontreplabsent'
        options.maintoken = 'livemaintoken'
        options.subordinatetoken = 'livesubordinatetoken'
        options.metaonly = False
        args = ['localhost:9292', 'localhost:9393']

        orig_img_service = glance_replicator.get_image_service
        try:
            glance_replicator.get_image_service = get_image_service
            updated = glance_replicator.replication_livecopy(options, args)
        finally:
            glance_replicator.get_image_service = orig_img_service

        self.assertEqual(len(updated), 2)

    def test_replication_livecopy_with_no_args(self):
        args = []
        command = glance_replicator.replication_livecopy
        self.assertTrue(check_no_args(command, args))

    def test_replication_livecopy_with_bad_args(self):
        args = ['aaa', 'bbb']
        command = glance_replicator.replication_livecopy
        self.assertTrue(check_bad_args(command, args))

    def test_replication_compare(self):
        options = UserDict.UserDict()
        options.chunksize = 4096
        options.dontreplicate = 'dontrepl dontreplabsent'
        options.maintoken = 'livemaintoken'
        options.subordinatetoken = 'livesubordinatetoken'
        options.metaonly = False
        args = ['localhost:9292', 'localhost:9393']

        orig_img_service = glance_replicator.get_image_service
        try:
            glance_replicator.get_image_service = get_image_service
            differences = glance_replicator.replication_compare(options, args)
        finally:
            glance_replicator.get_image_service = orig_img_service

        self.assertTrue('15648dd7-8dd0-401c-bd51-550e1ba9a088' in differences)
        self.assertEqual(differences['15648dd7-8dd0-401c-bd51-550e1ba9a088'],
                         'missing')
        self.assertTrue('37ff82db-afca-48c7-ae0b-ddc7cf83e3db' in differences)
        self.assertEqual(differences['37ff82db-afca-48c7-ae0b-ddc7cf83e3db'],
                         'diff')

    def test_replication_compare_with_no_args(self):
        args = []
        command = glance_replicator.replication_compare
        self.assertTrue(check_no_args(command, args))

    def test_replication_compare_with_bad_args(self):
        args = ['aaa', 'bbb']
        command = glance_replicator.replication_compare
        self.assertTrue(check_bad_args(command, args))


class ReplicationUtilitiesTestCase(test_utils.BaseTestCase):
    def test_check_upload_response_headers(self):
        glance_replicator._check_upload_response_headers({'status': 'active'},
                                                         None)

        d = {'image': {'status': 'active'}}
        glance_replicator._check_upload_response_headers({},
                                                         jsonutils.dumps(d))

        self.assertRaises(
            glance_replicator.UploadException,
            glance_replicator._check_upload_response_headers, {}, None)

    def test_image_present(self):
        client = FakeImageService(None, 'noauth')
        self.assertTrue(glance_replicator._image_present(
            client, '5dcddce0-cba5-4f18-9cf4-9853c7b207a6'))
        self.assertFalse(glance_replicator._image_present(
            client, uuid.uuid4()))

    def test_dict_diff(self):
        a = {'a': 1, 'b': 2, 'c': 3}
        b = {'a': 1, 'b': 2}
        c = {'a': 1, 'b': 1, 'c': 3}
        d = {'a': 1, 'b': 2, 'c': 3, 'd': 4}

        # Only things that the first dict has which the second dict doesn't
        # matter here.
        self.assertFalse(glance_replicator._dict_diff(a, a))
        self.assertTrue(glance_replicator._dict_diff(a, b))
        self.assertTrue(glance_replicator._dict_diff(a, c))
        self.assertFalse(glance_replicator._dict_diff(a, d))
