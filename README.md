# rhcos-aliyun-pruner

```bash
$ ./main.py --help
usage: main.py [-h] [--dry-run] [--debug] [--filename FILENAME] release

positional arguments:
  release              OCP release to operate on

optional arguments:
  -h, --help           show this help message and exit
  --dry-run            Just print what would happen
  --debug, -d          Enable debug logging
  --filename FILENAME  Path to file where bootimage data can be recorded; will allow for faster execution if script is run multiple times
```

Run the script with i.e. `./main.py 4.10`

Should work with OCP 4.10 + 4.11

Requires that the `ALIYUN_ACCESS_KEY_ID` and `ALIYUN_ACCESS_KEY_SECRET` environment variables are configured.

The Aliyun account provided should have write access to the images that are being queried.

Initial execution of this script may take a long, long time depending on the amount of builds made for a release.

## What does this do?

1. Check the git history of the `openshift/installer` repo for the specified release branch (i.e. `release-4.10`, `release-4.11`) for any changes to the bootimage metadata in `data/data/coreos/rhcos.json`
2. Records the RHCOS build IDs and Aliyun images uploaded to the various regions
3. Checks all the builds ever made for an RHCOS release (i.e. 4.10) by parsing builds.json for all the build IDs
4. Checks each RHCOS build ID to see if a Aliyun images were uploaded
5. If a build ID was found in the `openshift/installer` commit history, apply a tag of `bootimage=true`. Otherwise apply the tag `bootimage=false`
6. If an image is tagged with `bootimage=false`, delete the image from the Aliyun region
7. Once complete, the build IDs and affected images/regions are recorded in a file that can be used as an input for subsequent runs.
