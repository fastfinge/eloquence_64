#!/usr/bin/python

import os,re,sys,time
if (sys.version_info < (3, 0)):
    raise Exception("Python 3 required")
import urllib.request
import shutil
import tempfile
import zipfile

ORIGINAL_FILE_NAME = "eloquence_original.nvda-addon"
FILE_NAME = "eloquence.nvda-addon"

def updateZip(zipname, filename, filedata):
    # generate a temp file
    tmpfd, tmpname = tempfile.mkstemp(dir=os.path.dirname(zipname))
    os.close(tmpfd)

    # create a temp copy of the archive without filename            
    with zipfile.ZipFile(zipname, 'r') as zin:
        with zipfile.ZipFile(tmpname, 'w') as zout:
            zout.comment = zin.comment # preserve the comment
            for item in zin.infolist():
                if item.filename != filename:
                    zout.writestr(item, zin.read(item.filename))

    # replace with the temp archive
    os.remove(zipname)
    #print(f"os.rename({tmpname}, {zipname})")
    # For some really weird reason the following command not always works in certain conditions
    # So replacing it with an external call
    #os.rename(tmpname, zipname)
    os.system(f"rename {tmpname} {zipname}")
    time.sleep(1)

    # now add filename with its new data
    with zipfile.ZipFile(zipname, mode='a', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(filedata, filename)


if not os.path.exists(ORIGINAL_FILE_NAME):
    print("Downloading...")
    with urllib.request.urlopen('https://github.com/pumper42nickel/eloquence_threshold/releases/download/v0.20210417.01/eloquence.nvda-addon') as response:
        with open(ORIGINAL_FILE_NAME, "wb") as f:
            shutil.copyfileobj(response, f)
print("Patching...")
shutil.copyfile(ORIGINAL_FILE_NAME, FILE_NAME)
updateZip(FILE_NAME, "synthDrivers/eloquence.py", "eloquence.py")
updateZip(FILE_NAME, "synthDrivers/_eloquence.py", "_eloquence.py")
updateZip(FILE_NAME, "synthDrivers/_ipc.py", "_ipc.py")
updateZip(FILE_NAME, "synthDrivers/__multiprocessing.pyd", "_multiprocessing.pyd")
updateZip(FILE_NAME, "synthDrivers/_multiprocessing.pyd", "_multiprocessing.pyd")
updateZip(FILE_NAME, "synthDrivers/multiprocessing/dummy/__init__.py", "multiprocessing/dummy/__init__.py")
updateZip(FILE_NAME, "synthDrivers/multiprocessing/dummy/connection.py", "multiprocessing/dummy/connection.py")
updateZip(FILE_NAME, "synthDrivers/multiprocessing/__init__.py", "multiprocessing/__init__.py")
updateZip(FILE_NAME, "synthDrivers/multiprocessing/connection.py", "multiprocessing/connection.py")
updateZip(FILE_NAME, "synthDrivers/multiprocessing/context.py", "multiprocessing/context.py")
updateZip(FILE_NAME, "synthDrivers/multiprocessing/forkserver.py", "multiprocessing/forkserver.py")
updateZip(FILE_NAME, "synthDrivers/multiprocessing/heap.py", "multiprocessing/heap.py")
updateZip(FILE_NAME, "synthDrivers/multiprocessing/managers.py", "multiprocessing/managers.py")
updateZip(FILE_NAME, "synthDrivers/multiprocessing/pool.py", "multiprocessing/pool.py")
updateZip(FILE_NAME, "synthDrivers/multiprocessing/popen_fork.py", "multiprocessing/popen_fork.py")
updateZip(FILE_NAME, "synthDrivers/multiprocessing/popen_forkserver.py", "multiprocessing/popen_forkserver.py")
updateZip(FILE_NAME, "synthDrivers/multiprocessing/popen_spawn_posix.py", "multiprocessing/popen_spawn_posix.py")
updateZip(FILE_NAME, "synthDrivers/multiprocessing/popen_spawn_win32.py", "multiprocessing/popen_spawn_win32.py")
updateZip(FILE_NAME, "synthDrivers/multiprocessing/process.py", "multiprocessing/process.py")
updateZip(FILE_NAME, "synthDrivers/multiprocessing/queues.py", "multiprocessing/queues.py")
updateZip(FILE_NAME, "synthDrivers/multiprocessing/reduction.py", "multiprocessing/reduction.py")
updateZip(FILE_NAME, "synthDrivers/multiprocessing/resource_sharer.py", "multiprocessing/resource_sharer.py")
updateZip(FILE_NAME, "synthDrivers/multiprocessing/resource_tracker.py", "multiprocessing/resource_tracker.py")
updateZip(FILE_NAME, "synthDrivers/multiprocessing/shared_memory.py", "multiprocessing/shared_memory.py")
updateZip(FILE_NAME, "synthDrivers/multiprocessing/sharedctypes.py", "multiprocessing/sharedctypes.py")
updateZip(FILE_NAME, "synthDrivers/multiprocessing/spawn.py", "multiprocessing/spawn.py")
updateZip(FILE_NAME, "synthDrivers/multiprocessing/syncronize.py", "multiprocessing/synchronize.py")
updateZip(FILE_NAME, "synthDrivers/multiprocessing/util.py", "multiprocessing/util.py")
updateZip(FILE_NAME, "manifest.ini", "manifest.ini")
updateZip(FILE_NAME, "synthDrivers/eloquence_host32.exe", "dist/eloquence_host32.exe")
print(f"Created {FILE_NAME}")

