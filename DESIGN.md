# Pruning FCOS/RHCOS build artifacts

## Immediate Need

- tooling to prune RHCOS Aliyun images in the ART account across all supported regions

## Immediate Requirements

1. (CRITICAL) Must not remove artifacts which have been used in bootimage PRs
2. Can be run in an automated fashion
3. Should have a `--dry-run` equivalent for testing purposes
4. Should work for 4.10 + 4.11 releases
5. Should work for x86_64 builds
6. Should work across all Aliyun regions
7. Should work with Aliyun images marked public

## Longer Term Requirements

1. Should be able to operate on any build architectures
2. Should be able to operate on any release (i.e. 4.10, 4.9, 4.8)
3. Should be extensible to handle any new artifacts/platforms/clouds
4. Should be able to work with FCOS builds
5. Should be native functionality in `cosa`

## Immediate Implementation Tasks

- Ability to checkout branch of `openshift/installer` and inspect `git history` of the stream metadata. And save the various versions where the metadata changed.
  - <https://github.com/openshift/installer/blob/master/data/data/coreos/rhcos.json>
  - May be tricky to implement because the location/format of the metadata has changed after 4.9
    - i.e. <https://github.com/openshift/installer/blob/release-4.8/data/data/rhcos.json>
  I think we only need to care about *released* versions, i.e. images that made it into quay.io/openshift-art-dev/.  But we could be conservative too

- Ability to tag Aliyun images with metadata that indicates they should not be pruned
  - example cli command: `aliyun ecs TagResources --RegionId us-east-1 --ResourceType image --ResourceId.1 m-0xi47nhv1zat67he9n4j --Tag.1.Key bootimage --Tag.1.Value true`
  - SDK function should be similarly named (see API docs - <https://partners-intl.aliyun.com/help/en/doc-detail/110424.html>)

- Ability to enumerate builds for a release (i.e. 4.10) and determine if Aliyun images are present in the build
  - Fetch the `builds.json` from the redirector (<https://rhcos-redirector.apps.art.xq1c.p1.openshiftapps.com/art/storage/releases/>) for the relase
  - Fetch each `meta.json` from the redirector, determine if it has `aliyun` key

- Ability to delete images from Aliyun cloud if the image does not have the defined tag applied
  - example cli command for querying: `aliyun ecs DescribeImages --RegionId us-east-1 --ImageId m-0xi7b9pbfh758edvv8ei | jq .Images.Image[].Tags`
  - If the image is marked public (check `IsPublic`), needs to marked private before it can be deleted (`aliyun ecs ModifyImageSharePermission --RegionId us-east-1 --ImageId m-0xi7b9pbfh758edvv8ei --IsPublic false`)
  - example cli command for deletion: `aliyun ecs DeleteImage --RegionId us-east-1 --ImageId m-0xi7b9pbfh758edvv8ei`

## Longer Term Implementation Tasks

### Catch-all Notes Location

- original hackmd: <https://hackmd.io/mE1sW5qKR8S-T5-GQQ1iBg>
