#!/usr/bin/python

# Utility script for pruning Aliyun images from an account
#
# Must have ALIYUN_ACCESS_KEY_ID and ALIYUN_ACCESS_KEY_SECRET env vars defined
#
# See also the OpenAPI explorer for making sense of the API/SDK
#  - https://api.aliyun.com/#/?product=Ecs

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
from urllib.request import urlopen

import git

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
# Returns a dict keyed off buildid where values are a list of {region_id:image_id} pairs
def get_images_not_tagged(bootimages):
    nottagged = {}

    for bootimage in bootimages:
        if bootimage not in nottagged:
            nottagged[bootimage] = []
        logging.info(f"Searching for untagged images in build {bootimage}")
        for region in bootimages[bootimage]:
            imageid = bootimages[bootimage][region]['image']
            logging.debug(f"Getting image info for {imageid} in {region}")
            response = get_image_info(region, imageid)
            for image in response['Images']['Image']:
                tagfound = False
                for tag in image['Tags']['Tag']:
                    if tag['TagKey'] == 'bootimage' and \
                      (tag['TagValue'] == 'true' or tag['TagValue'] == 'false'):
                        break
                if tagfound is False:
                    nottagged[bootimage].append({'region_id': region, 'image_id': image['ImageId']})
    return nottagged


# Get all images in builds.json and check the build meta.json to see
# if we had an aliyun artifact created
#
# Takes a release (i.e. 4.10) and the json dict as the input
#
# Returns a dict keyed off of build ID that contains {region_id: image_id} pairs
def parse_release(release, json_file):
    releases = {}
    logging.debug(f"Getting all builds for RHCOS {release}")
    jsonurl = urlopen("%srhcos-%s/builds.json" % (REDIRECTOR_URL, release))
    buildjson = json.loads(jsonurl.read())

    for build in (buildjson['builds']):
        buildid = build['id']
        if buildid in json_file:
            logging.debug(f"Build ID: {buildid} found in file")
            continue
        arch = build['arches'][0]
        buildid_int = int((buildid.replace('.','')).replace('-',''))
        # Look only for builds after the aliyun inclusion
        # TODO: we can improve it keeping a record for the build we already checked
        if buildid_int >= int(FIRSTRELEASE[arch][release][0]):
            metajsonURL = ("%srhcos-%s/%s/%s/meta.json" % (REDIRECTOR_URL, release, buildid ,arch))
            logging.debug(f"Checking {buildid} for Aliyun uploads")
            jsonurl = urlopen(metajsonURL)
            metajson = json.loads(jsonurl.read())
            if 'aliyun' in metajson:
                # Create the same output we have for bootimages
                logging.debug(f"Recording Aliyun images for {buildid}")
                releases[buildid] = {}
                for entry in metajson['aliyun']:
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

    # TagResourceRequest() is idempotent, so we can just call it blindly without
    # checking if the tag=value is already there
    client = create_client(region_id)
    tag_request = TagResourcesRequest()
    tag_request.set_ResourceType("image")
    tag_request.set_ResourceIds([image_id])
    tag_request.set_protocol_type('https')
    tag_request.set_Tags([
        {
            "Key": tag_key,
            "Value": tag_value
        }
    ])
    tag_resp = run_cmd([client, tag_request])
    if tag_resp == 'dry_run':
        return json.dumps('{}')
    return json.loads(tag_resp.decode("utf-8"))

# Tag an image with `key:value`; defaults to `bootimage:false` and
# return a json file with region_id and image_id
#
# Accepts image_list list as argument; optionally a tag key and
# tag value
#
# Returns a JSON file path
def tag_image_and_save_to_file(image_list, file_path, tag_key=None, tag_value=None):
    if tag_key is None:
        tag_key = "bootimage"
    if tag_value is None:
        tag_value = "false"

    new_data = {}
    for buildid in image_list:
        for region in image_list[buildid]:
            if buildid not in new_data:
                new_data[buildid] = []
            region_id = region['region_id']
            image_id = region['image_id']
            tag_image(region_id, image_id, tag_key, tag_value)
            new_data[buildid].append({ "region": region_id, "image": image_id, "deleted": False})

    if os.path.exists(file_path):
        logging.debug(f'Found existing {file_path}; updating with new data')
        with open(file_path, 'r+') as f:
            data = json.load(f)
            data.update(new_data)
            f.seek(0)
            f.write(json.dumps(data))
    else:
        logging.debug(f"Creating new {file_path} with tag data")
        with open(file_path, 'w') as f:
            f.write(json.dumps(new_data))

    return


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

    try:
        describe_resp = client.do_action_with_exception(describe_req)
    except (ClientException, ServerException) as e:
        logging.error("Unable to describe {}: {}".format(image_id, e))
        sys.exit(1)

    return json.loads(describe_resp.decode("utf-8"))


# Utility function to mark an image public/private
#
# Takes region_id str, image_id str, public boolean
#
# Returns a JSON doc of the response from the API
def change_visibility(region_id, image_id, public=False):
    # changing IsPublic via ModifyImageSharePermissionRequest is not idempotent,
    # so we have to check to see if the value is already set properly
    image_info = get_image_info(region_id, image_id)
    if image_info['Images']['Image'][0]['IsPublic'] == public:
        logging.debug(f"{image_id} is already marked IsPublic={public}")
        # return empty JSON doc
        return json.dumps("{}")

    client = create_client(region_id)
    modify_req = ModifyImageSharePermissionRequest()
    modify_req.set_ImageId(image_id)
    modify_req.set_IsPublic(public)
    modify_req.set_protocol_type('https')

    logging.debug(f"Marking {image_id} in {region_id} with IsPublic={public}")
    modify_resp = run_cmd([client, modify_req])
    if modify_req == 'dry_run':
        return json.dumps('{}')
    return json.loads(modify_resp.decode("utf-8"))


# Deletes an image from the cloud.
#
# Takes the path to the JSON doc generated by tag_image_and_save_to_file()
#
# Updates the JSON doc with results
def delete_images(file_path):
    if os.path.exists(file_path):
        logging.debug(f"Found file {file_path}")
        with open(file_path, 'r') as f:
            deleted_images_json = json.load(f)
    else:
        logging.error(f"Unable to find {file_path}")
        sys.exit(1)

    for buildid in deleted_images_json:
        logging.info(f"Working through images/regions for {buildid} to delete...")
        # enumerate the list of regions/images
        for pos, item in enumerate(deleted_images_json[buildid]):
            region_id = item['region']
            image_id = item['image']
            # if the image hasn't been marked deleted, remove it, and then update
            # the 'deleted' key to True
            if not item["deleted"]:
                # we have to mark the image private before deleting it
                image_info = get_image_info(region_id, image_id)
                if image_info['Images']['Image'][0]['IsPublic'] is True:
                    change_visibility(region_id, image_id, public=False)

                client = create_client(region_id)
                delete_req = DeleteImageRequest()
                delete_req.set_ImageId(image_id)
                delete_req.set_protocol_type('https')

                logging.warning(f"---Deleting {image_id} in {region_id}")
                delete_req = run_cmd([client, delete_req])
                if delete_req  == 'dry_run':
                    continue
                deleted_images_json[buildid][pos]["deleted"] = True
            else:
                logging.debug(f"{image_id} in {region_id} already marked as deleted")

        with open(file_path, 'w') as f:
            json.dump(deleted_images_json, f)


# Run the commands passed in dry mode or execute them, defaults to 'dru_run=True'
#
# Accepts to_run list, silent boolean, ignore_error boolean and dry_run boolean
# as arguments;
#
# Returns `'dry_run` str or result of the the passed command
def run_cmd(command, silent = False, ignore_error = False):
    action = command[1]._action_name
    params = command[1]._params
    request = command[1]
    client = command[0]
    try:
        if DRY_RUN:
            print("Running --- Dry Run ----")
            print("Action to perfom:%s" % (action))
            print("Parameters:%s" % (params))
            return 'dry_run'
        else:
            result = client.do_action_with_exception(request)
            return result
    except (ClientException, ServerException) as e:
        if not ignore_error:
            logging.error("Unable to perfom action:{} with: {}. {}".format(action, params, e))
            sys.exit(1)
        return False


# Finds the Aliyun images included in a bootimage bump to openshift/installer
# given an OCP version string
#
# Takes a release version (i.e. 4.10) as an argument
#
# Returns a dict keyed off of build ID with values like {region_id: {release: build_id, image: image_id}}
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
    parser.add_argument('--filename', help="Path to file where bootimage data can be recorded; will allow for faster execution if script is run multiple times", default="deleted_images.json")
    args = parser.parse_args()

    image_list = {}
    deleted_images_json = {}

    if 'ALIYUN_ACCESS_KEY_ID' not in os.environ or 'ALIYUN_ACCESS_KEY_SECRET' not in os.environ:
        logging.error('Must have ALIYUN_ACCESS_KEY_ID and ALIYUN_ACCESS_KEY_SECRET env variables set')
        sys.exit(1)

    global DRY_RUN
    DRY_RUN = False
    if args.dry_run:
        DRY_RUN = True

    logging.basicConfig(level=logging.INFO)
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)

    if args.filename:
        deleted_images_filename = args.filename

    # preload images that should be deleted
    if os.path.exists(deleted_images_filename):
        logging.debug(f"Found file: {deleted_images_filename}")
        with open(deleted_images_filename, 'r') as f:
            deleted_images_json = json.load(f)

    # # get aliyun images in the installer
    logging.info("Parsing the installer code")
    bootimages = parse_openshift_installer(args.release)
    logging.info("Getting untagged images from installer data")
    bootimages = get_images_not_tagged(bootimages)

    # get builds with aliyun uploads from a builds.json
    logging.info("Finding builds with Aliyun uploads from builds.json")
    aliyun_releases = parse_release(args.release, deleted_images_json)
    logging.info("Finding untagged images in all Aliyun uploads")
    aliyun_releases = get_images_not_tagged(aliyun_releases)

    if len(aliyun_releases) == 0:
        logging.error("Didn't find any images to tag or delete")
        sys.exit(1)

    # find the builds from builds.json that are not in bootimages
    for buildid in aliyun_releases:
        if buildid in bootimages:
            for region in aliyun_releases[buildid]:
                image_id =region['image_id']
                region = region['region_id']
                tag_image(region, image_id, tag_key="bootimage", tag_value="true")
        elif buildid in deleted_images_json:
            logging.debug(f"Found {buildid} in {deleted_images_filename}; skipping tagging")
            continue
        else:
            if buildid not in image_list:
                image_list[buildid] = []
            # region here is a '{region_id: image_id}' dict
            for region in aliyun_releases[buildid]:
                image_list[buildid].append(region)

    tag_image_and_save_to_file(image_list, deleted_images_filename)
    delete_images(deleted_images_filename)

if __name__ == "__main__":
    main()
