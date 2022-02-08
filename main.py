#!/usr/bin/python

# Utility script for pruning Aliyun images from an account
#
# Assumes that there is a valid set of credentials at `~/.aliyun/credentials`
# https://github.com/aliyun/alibabacloud-python-sdk/blob/master/docs/1-Client-EN.md
#
# Though I had to use ALIBABA_CLOUD_CREDENTIALS_FILE to make it work correctly;
# the docs for this SDK are not great.
#
#  https://github.com/aliyun/credentials-python/blob/master/alibabacloud_credentials/providers.py#L356-L363
#
# See also the OpenAPI explorer for making sense of the API/SDK
#  - https://api.aliyun.com/#/?product=Ecs

import argparse
import git
import json
import logging
import os
import shutil
import tempfile


from alibabacloud_credentials.client import Client as CredClient
from alibabacloud_ecs20140526.models import DeleteImageRequest, \
                                            TagResourcesRequest, \
                                            TagResourcesRequestTag
from alibabacloud_ecs20140526.client import Client
from alibabacloud_tea_openapi.models import Config
from aliyunsdkecs.request.v20140526.DescribeImagesRequest import DescribeImagesRequest
from urllib.request import urlopen

OPENSHIFT_INSTALL_GIT = "https://github.com/openshift/installer"
REDIRECTOR_URL = "https://rhcos-redirector.apps.art.xq1c.p1.openshiftapps.com/art/storage/releases/"

FIRSTRELEASE = {}
FIRSTRELEASE['aarch64'] = 0
FIRSTRELEASE['ppc64le'] = 0
FIRSTRELEASE['s390x'] = 0
FIRSTRELEASE['x86_64'] = {'4.10': '410842021120118210', '4.11': '411842022020718390'}

# creates an Aliyun client for a region
def create_client(region_id):
    cred = CredClient()
    config = Config(credential=cred)
    config.region_id = region_id

    client = Client(config)
    return client

def get_images_not_tagged(bootimages):
    request = DescribeImagesRequest()
    nottagged = []

    for bootimage in bootimages:
        for region in bootimages[bootimage]:
            imageid = bootimages[bootimage][region]['image']
            request.set_ImageId(imageid)
            client = create_client(region)
            response = client.do_action_with_exception(request)
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
# tag an image with `bootimage:true`
def tag_image(region_id, image_id):
    tag_key = "bootimage"
    tag_value = "true"

    bootimage_tag = TagResourcesRequestTag(key=tag_key, value=tag_value)
    client = create_client(region_id)
    tag_request = TagResourcesRequest(resource_type="image", resource_id=[image_id], tag=[bootimage_tag])
    # creating a client with a region_id set doesn't propogate to the request for some reason
    tag_request.region_id = region_id
    tag_response = client.tag_resources(tag_request)


# utility function to get info about an image
def get_image_info(region_id, image_id):
    describe_req = DescribeImagesRequest()
    describe_req.image_id = image_id
    describe_req.region_id  = region_id

    client = create_client(region_id)
    logging.debug(f"Sending DescribeImages request for {image_id}")
    describe_resp = client.describe_images(describe_req)

    return describe_resp


# will delete an image as long as the check_tag_key does not equal check_tag_value
def delete_image(region_id, image_id, check_tag_key=None, check_tag_value=None):
    if check_tag_key is None:
        check_tag_key = "bootimage"
    if check_tag_value is None:
        check_tag_value = "true"

    logging.debug(f"Getting info for {image_id}")


    image_info = get_image_info(region_id, image_id)
    # image_info is a DescribeImagesResponse object
    #
    # DescribeImagesResponse
    #   -> body (DescribeImagesResponseBody)
    #      -> images (DescribeImagesResponseBodyImages)
    #          -> image (list of DescribeImagesResponseBodyImagesImage)
    #             -> tags (DescribeImagesResponseBodyImagesImageTags)
    #                -> tag (list of DescribeImagesResponseBodyImagesImageTagsTag)
    #
    # there should only be a single item in the list since image IDs are unique
    # so just iterate over the tags
    for tag in image_info.body.images.image[0].tags.tag:
        if tag.tag_key == check_tag_key and tag.tag_value == check_tag_value:
            logging.warning(f"{image_id} is tagged with {check_tag_key}={check_tag_value}; will not delete")
            return

    logging.debug(f"Did not find any tags preventing deletion for {image_id}")
    delete_req = DeleteImageRequest()
    delete_req.image_id = image_id
    delete_req.region_id = region_id

    logging.debug(f"Created DeleteImages request for {image_id}")
    print(delete_req)
    # TODO: actual calls to do the deletion are commented out until we have
    # better support for `--dry-run`
    #client = create_client(region_id)
    #delete_resp = client.delete_image(delete_req)


# given an OCP version string, checkout the repo, find the aliyun images in
# rhcos.json and return a dict with them
#
# aliyun_images[build_id] = {region_id: {release: build_id, image: image_id},...}
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
    releases = (parse_release(args.release))
    images = get_images_not_tagged(releases)
    #tag_image(region_id="us-east-1", image_id="m-0xi47nhv1zat67he9n4j")
    #desc_resp = get_image_info("us-west-1", "m-rj947nhv1zas8vulsa3p")
    #delete_image("us-west-1", "m-rj947nhv1zas8vulsa3p")
    # test values
    tag_image(region_id="us-east-1", image_id="m-0xi47nhv1zat67he9n4j")


if __name__ == "__main__":
    main()
