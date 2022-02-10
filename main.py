#!/usr/bin/python

# Utility script for pruning Aliyun images from an account
#
# Must have ALIYUN_ACCESS_KEY_ID and ALIYUN_ACCESS_KEY_SECRET env vars defined
#
# See also the OpenAPI explorer for making sense of the API/SDK
#  - https://api.aliyun.com/#/?product=Ecs

import argparse
import git
import json
import logging
import os
import shutil
import sys
import tempfile
from urllib.request import urlopen

from aliyunsdkcore.client import AcsClient
from aliyunsdkcore.acs_exception.exceptions import ClientException
from aliyunsdkcore.acs_exception.exceptions import ServerException
from aliyunsdkecs.request.v20140526.DeleteImageRequest import DeleteImageRequest
from aliyunsdkecs.request.v20140526.DescribeImagesRequest import DescribeImagesRequest
from aliyunsdkecs.request.v20140526.ModifyImageSharePermissionRequest import ModifyImageSharePermissionRequest
from aliyunsdkecs.request.v20140526.TagResourcesRequest import TagResourcesRequest


OPENSHIFT_INSTALL_GIT = "https://github.com/openshift/installer"
REDIRECTOR_URL = "https://rhcos-redirector.apps.art.xq1c.p1.openshiftapps.com/art/storage/releases/"

# build out a dict where the first Aliyun artifact appeared to speed up the
# interation through all the builds of a release.
FIRSTRELEASE = {}
FIRSTRELEASE['aarch64'] = 0
FIRSTRELEASE['ppc64le'] = 0
FIRSTRELEASE['s390x'] = 0
FIRSTRELEASE['x86_64'] = {'4.10': '410842021120118210', '4.11': '411842022020718390'}


# Creates an Aliyun client for a region
#
# Takes a region_id str as argument
#
# Returns an AcsClient object
def create_client(region_id):
    client = AcsClient(region_id=region_id)
    return client


# Utility function to get a list of images that are not tagged with "bootimage"
#
# Takes a dict from parse_openshift_installer() as an argument
#
# Returns a list of {region_id:image_id} pairs
def get_images_not_tagged(bootimages):
    request = DescribeImagesRequest()
    nottagged = []

    for bootimage in bootimages:
        for region in bootimages[bootimage]:
            imageid = bootimages[bootimage][region]['image']
            request.set_ImageId(imageid)
            request.set_protocol_type('https')
            client = create_client(region)
            try:
                response = client.do_action_with_exception(request)
            except (ClientException, ServerException) as e:
                logging.error("Unable to describe {}: {}".format(imageid, e))
                sys.exit(1)

            response = json.loads(response.decode("utf-8"))
            for image in response['Images']['Image']:
                tagfound = False
                for tag in image['Tags']['Tag']:
                    if tag['TagKey'] == 'bootimage' and \
                      (tag['TagValue'] == 'true' or tag['TagValue'] == 'false'):
                        tagfound = True
                        break
                if tagfound is False:
                    nottagged.append({'region_id': region, 'image_id': image['ImageId']})
    return nottagged


# Get all images in builds.json and check the build meta.json to see
# if we had an aliyun artifact created
#
# Takes a release (i.e. 4.10) as the input
#
# Returns a dict keyed off of build ID that contains {region_id: image_id} pairs
def parse_release(release):
    releases = {}
    jsonurl = urlopen("%srhcos-%s/builds.json" % (REDIRECTOR_URL, release))
    buildjson = json.loads(jsonurl.read())

    for build in (buildjson['builds']):

        arch = build['arches'][0]
        buildid = build['id']
        buildid_int = int((buildid.replace('.','')).replace('-',''))
        # Look only for builds after the aliyun inclusion
        # TODO: we can improve it keeping a record for the build we already checked
        if buildid_int >= int(FIRSTRELEASE[arch][release][0]):
            metajsonURL = ("%srhcos-%s/%s/%s/meta.json" % (REDIRECTOR_URL, release, buildid ,arch))
            jsonurl = urlopen(metajsonURL)
            metajson = json.loads(jsonurl.read())
            if 'aliyun' in metajson:
                # Create the same output we have for bootimages
                releases[buildid] = {}
                for entry in  metajson['aliyun']:
                    releases[buildid][entry['name']] = {'image':entry['id']}
    return releases


# Tag an image with `key:value`; defaults to `bootimage:false`
#
# Accepts region_id str and image_id str as arguments; optionally a tag key and
# tag value
#
# Returns a JSON doc of the response from the API
def tag_image(region_id, image_id, tag_key=None, tag_value=None):
    if tag_key is None:
        tag_key = "bootimage"
    if tag_value is None:
        tag_value = "false"

    client = create_client(region_id)
    tag_request = TagResourcesRequest()
    tag_request.set_ResourceType("image")
    tag_request.set_ResourceId(image_id)
    tag_request.set_protocol_type('https')
    tag_request.set_Tags([
        {
            "Key": tag_key,
            "Value": tag_value
        }
    ])

    try:
        tag_resp = client.do_action_with_exception(tag_request)
    except (ClientException, ServerException) as e:
        logging.error("Unable to tag {}: {}".format(image_id, e))
        sys.exit(1)

    return json.loads(tag_resp.decode("utf-8"))


# Utility function to get info about an image
#
# Takes region_id str and image_id str as arguments
#
# Returns a JSON doc of the response from the API
def get_image_info(region_id, image_id):
    client = create_client(region_id)
    describe_req = DescribeImagesRequest()
    describe_req.set_ImageId(image_id)
    describe_req.set_protocol_type('https')

    logging.debug(f"Sending DescribeImages request for {image_id}")
    describe_resp = client.do_action_with_exception(describe_req)

    return json.loads(describe_resp.decode("utf-8"))


# Utility function to mark an image public/private
#
# Takes region_id str, image_id str, public boolean
#
# Returns a JSON doc of the response from the API
def change_visibility(region_id, image_id, public=False):
    client = create_client(region_id)
    modify_req = ModifyImageSharePermissionRequest()
    modify_req.set_ImageId(image_id)
    modify_req.set_IsPublic(public)
    modify_req.set_protocol_type('https')

    logging.debug(f"Marking {image_id} in {region_id} with IsPublic={public}")
    try:
        modify_resp = client.do_action_with_exception(modify_req)
    except (ClientException, ServerException) as e:
        logging.error("Unable to mark {} as public={}: {}".format(image_id, public, e))
        sys.exit(1)

    return json.loads(modify_resp.decode("utf-8"))

# Deletes an image from the cloud. Can optionally confirm that the image was not
# tagged with a key:value
#
# Takes a region_id str, image_id str as arguments. Optionally can take a tag
# and value to check for.
#
# Returns a JSON doc of the response from the API
def delete_image(region_id, image_id, check_tag_key=None, check_tag_value=None):
    if check_tag_key is not None and check_tag_value is not None:
        logging.debug(f"Checking for {check_tag_key}={check_tag_value} before deleting {image_id}")
        image_info = get_image_info(region_id, image_id)
        for tag in image_info['Images']['Image'][0]['Tags']['Tag']:
            if tag['TagKey'] == check_tag_key and tag['TagValue'] == check_tag_value:
                logging.warning(f"{image_id} is tagged with {check_tag_key}={check_tag_value}; will not delete")
                # return empty JSON doc
                return json.load("{}")

    logging.debug(f"Going to delete {image_id} in {region_id}")
    client = create_client(region_id)
    delete_req = DeleteImageRequest()
    delete_req.set_ImageId(image_id)
    delete_req.set_protocol_type('https')

    logging.warning(f"Deleting {image_id} in {region_id}")
    # TODO: actual calls to do the deletion are commented out until we have
    # better support for `--dry-run`
    # try:
    #     delete_resp = client.do_action_with_exception(delete_req)
    # except (ClientException, ServerException) as e:
    #     logging.error("Unable to delete {}: {}".format(image_id, e))
    #     sys.exit(1)
    # return json.loads(delete_resp.decode("utf-8"))


# Finds the Aliyun images included in a bootimage bump to openshift/installer
# given an OCP version string
#
# Takes a release version (i.e. 4.10) as an argument
#
# Returns oa dict keyed off of build ID with values like {region_id: {release: build_id, image: image_id}}
def parse_openshift_installer(release):
    tmpdir = tempfile.mkdtemp()
    rhcos_json_path = 'data/data/coreos/rhcos.json'
    full_rhcos_json_path = os.path.join(tmpdir, rhcos_json_path)
    full_release = "release-" + release

    logging.debug("Cloning repo")
    repo = git.Repo.clone_from(OPENSHIFT_INSTALL_GIT, tmpdir)
    logging.debug(f"Checking out branch {full_release}")
    repo.git.checkout(full_release)
    logging.debug("Getting commits")
    rhcos_commits = repo.iter_commits(paths=rhcos_json_path)

    # dict keyed off build id
    aliyun_images = {}
    for commit in rhcos_commits:
        logging.debug(f"Checking {commit.hexsha} for Aliyun images")
        repo.git.checkout(commit.hexsha)
        with open(full_rhcos_json_path, 'r') as f:
            rhcos_json = json.load(f)

        if 'aliyun' in rhcos_json['architectures']['x86_64']['images']:
            build_id = rhcos_json['architectures']['x86_64']['artifacts']['aliyun']['release']
            logging.debug(f"Recording {build_id} as having Aliyun images")
            aliyun_images[build_id] = rhcos_json['architectures']['x86_64']['images']['aliyun']['regions']

    shutil.rmtree(tmpdir)
    return aliyun_images


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('release', help="OCP release to operate on")
    parser.add_argument('--dry-run', help="Just print what would happen", action='store_true')
    parser.add_argument('--debug', '-d', help="Enable debug logging", action='store_true')
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)


    ### testing functions
    #bootimages = parse_openshift_installer(args.release)
    #print(bootimages)
    #releases = (parse_release(args.release))
    #images = get_images_not_tagged(releases)
    #tag_image(region_id="us-east-1", image_id="m-0xi47nhv1zat67he9n4j")
    #desc_resp = get_image_info("us-west-1", "m-rj947nhv1zas8vulsa3p")
    #print(desc_resp)
    #delete_image("us-west-1", "m-rj947nhv1zas8vulsa3p")
    #change_visibility("us-east-1", "m-0xi7bf33rrl9dtvr3zbp", True)


if __name__ == "__main__":
    main()
