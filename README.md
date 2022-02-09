# rhcos-aliyun-pruner

Run the script with `./main.py 4.10`

Should work with OCP 4.10 + 4.11


## Devel Notes

0. Determine order of operations

a. checking openshift/installer code/data
b. tag bootimages from step a. with `bootimage=true`
c. tag other images with `bootimage=false`
d. delete images with `bootimage=false`

## TODO

~~1. Change API calls to use OpenAPI Explorer suggestions~~
1. Improve cloud API requests to use try...except... model
2. Enhance tagging function to record images with `bootimage=false` in local file
3. Enhance delete function to look for local file from #2
4. Change default operation for script to be dry-run; add flag to indicate destruction of images?
5. Add some additional logging in functions; log all operations to file?

next next steps: run script with dry-run on prod data
