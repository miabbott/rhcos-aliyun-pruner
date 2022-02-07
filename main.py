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

import argparse
import git
import json
import logging
import os
import tempfile
import shutil

from alibabacloud_credentials.client import Client as CredClient
from alibabacloud_ecs20140526.models import DescribeImagesRequest, \
                                            TagResourcesRequest, \
                                            TagResourcesRequestTag
from alibabacloud_ecs20140526.client import Client
from alibabacloud_tea_openapi.models import Config


OPENSHIFT_INSTALL_GIT = "https://github.com/openshift/installer"
REDIRECTOR_URL = "https://rhcos-redirector.apps.art.xq1c.p1.openshiftapps.com/art/storage/releases/"


# creates an Aliyun client for a region
def create_client(region_id):
    cred = CredClient()
    config = Config(credential=cred)
    config.region_id = region_id

    client = Client(config)
    return client


# tag an image with `bootimage:true`
def tag_image(region_id, image_id, tag_key=None, tag_value=None):
    if tag_key is None:
        tag_key = "bootimage"
    if tag_value is None:
        tag_value = "false"

    bootimage_tag = TagResourcesRequestTag(key=tag_key, value=tag_value)
    client = create_client(region_id)
    tag_request = TagResourcesRequest(resource_type="image", resource_id=[image_id], tag=[bootimage_tag])
    # creating a client with a region_id set doesn't propogate to the request for some reason
    tag_request.region_id = region_id
    tag_response = client.tag_resources(tag_request)


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

    bootimages = parse_openshift_installer(args.release)

    # tag all bootimages with "true"
    for build in bootimages:
        region_id = build['region_id']
        image_id = build['region_id']['image']
        tag_image(region_id, image_id, tag_value="true")

    # TODO: iterate over all builds.json and tag them with false
    #
    # pseudo code:
    #   for build in build_json:
    #       if build_id in bootimages:
    #           tag_image(region_id, image_id, tag_value=truie)
    #       else:
    #           tag_image(region_id, image_id, tag_value=false)


if __name__ == "__main__":
    main()
