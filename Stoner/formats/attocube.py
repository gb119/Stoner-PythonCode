# -*- coding: utf-8 -*-
"""Module to work with scan files from an AttocubeSPM running Daisy."""
__all__ = ["AttocubeScan"]
from os import path, pathsep
from copy import deepcopy
from glob import glob
import re
import importlib
from numpy import genfromtxt, linspace, meshgrid, array, product
from scipy.interpolate import griddata
from scipy.optimize import curve_fit

import h5py

from Stoner.compat import string_types, bytes2str
from Stoner.core.base import typeHintedDict
from Stoner.core.exceptions import StonerLoadError
from Stoner.Image import ImageStack, ImageFile, ImageArray

PARAM_RE = re.compile(r"^([\d\\.eE\+\-]+)\s*([\%A-Za-z]\S*)?$")
SCAN_NO = re.compile("SC_(\d+)")


def _raise_error(f, message="Not a valid hdf5 file."):
    """Try to clsoe the filehandle f and raise a StonerLoadError."""
    try:
        f.file.close()
        raise StonerLoadError(message)
    except Exception:
        raise StonerLoadError(message)


def _open_filename(filename):
    """Examine a file to see if it is an HDF5 file and open it if so.

    Args:
        filename (str): Name of the file to open

    Returns:
        (f5py.Group): Valid h5py.Group containg data/

    Raises:
        StonerLoadError if not a valid file.
    """
    parts = filename.split(pathsep)
    filename = parts.pop(0)
    group = ""
    while len(parts) > 0:
        if not path.exists(path.join(filename, parts[0])):
            group = "/".join(parts)
        else:
            path.join(filename, parts.pop(0))

    with open(filename, "rb") as sniff:  # Some code to manaully look for the HDF5 format magic numbers
        sniff.seek(0, 2)
        size = sniff.tell()
        sniff.seek(0)
        blk = sniff.read(8)
        if not blk == b"\x89HDF\r\n\x1a\n":
            c = 0
            while sniff.tell() < size and len(blk) == 8:
                sniff.seek(512 * 2 ** c)
                c += 1
                blk = sniff.read(8)
                if blk == b"\x89HDF\r\n\x1a\n":
                    break
            else:
                raise StonerLoadError("Couldn't find the HD5 format singature block")
    try:
        f = h5py.File(filename, "r+")
        for grp in group.split("/"):
            if grp.strip() != "":
                f = f[grp]
    except IOError:
        _raise_error(f, message=f"Failed to open {filename} as a n hdf5 file")
    except KeyError:
        _raise_error(f, message=f"Could not find group {group} in file {filename}")
    return f


def parabola(X, cx, cy, a, b, c):
    """A parabola in the X-Y plane for levelling an image."""
    x, y = X
    return a * (x - cx) ** 2 + b * (y - cy) ** 2 + c


def plane(X, a, b, c):
    """A plane equation for levelling an image."""
    x, y = X
    return a * x + b * y + c


class AttocubeScan(ImageStack):

    """ An ImageStack subclass that can load scans from the AttocubeScan SPM System.

    AttocubeScan represents a scan from an Attocube SPM system as a 3D stack of scan data with
    associated metadata. Indexing the AttocubeScan with either an integer or a partial match to one the
    signals saved in the scan will pull out that particular scan as a :py:class:`Stoner.Image.ImageFile`.

    If the scan was a dual pass scan with forwards and backwards data, then the root AttocubeScan will
    contain the common metadata derived from the Scan parameters and then two sub-stacks that represent
    the forwards ('fwd') and backwards ('bwd') scans.

    The AttocubeScan constructor will with take a *rrot name* of the scan - e.g. "SC_099" or alternatively
    a scan number integer. It will then look in the stack's directory for matching files and builds the scan stack
    from them. Currently, it uses the scan parameters.txt file and any ASCII stream files .asc files. 2D linescans
    are not currently supported or imported.

    The native file format for an AttocubeScan is an HDF5 file with a particilar structure. The stack is saved into
    an HDF5 group which then has a *type* and *module* attribute that specifies the class and module pf the Python
    object that created the group - sof for an AttocubeScan, the type attribute is *AttocubeScan*.

    There is a class method :py:meth:`AttocubeSca.read_HDF` to read the stack from the HDSF format and an instance method
    :py:meth:`AttocubeScan.to_HDF` that will save to either a new or existing HDF file format.

    The class provides other methods to regrid and flatten images and may gain other capabilities in the future.

    TODO:
        Implement load and save to/from multipage TIFF files.

    Attrs:
        scan_no (int):
            The scan number as defined in the Attocube software.
        compression (str):
            The HDF5 compression algorithm to use when writing files
        compression_opts (int):
            The lelbel of compression to use (depends on compression algorithm)

    """

    def __init__(self, *args, **kargs):

        args = list(args)

        if len(args) and isinstance(args[0], string_types):
            root_name = args.pop(0)
            scan = SCAN_NO.search(root_name)
            if scan:
                scan = int(scan.groups()[0])
            else:
                scan = -1
        elif len(args) and isinstance(args[0], int):
            scan = args.pop(0)
            root_name = f"SC_{scan:03d}"
        else:
            root_name = kargs.pop("root", None)
            scan = kargs.pop("scan", -1)

        regrid = kargs.pop("regrid", False)

        super(AttocubeScan, self).__init__(*args, **kargs)

        self._common_metadata = typeHintedDict()

        if root_name:
            self._load_files(root_name, regrid)

        self.scan_no = scan

        self._common_metadata["Scan #"] = scan

        self.compression = "gzip"
        self.compression_opts = 6

    def __clone__(self, other=None, attrs_only=False):
        """Do whatever is necessary to copy attributes from self to other.

        Note:
            We're in the base class here, so we don't call super() if we can't handle this, then we're stuffed!


        """
        other = super(AttocubeScan, self).__clone__(other, attrs_only)
        other._common_metadata = deepcopy(self._common_metadata)
        return other

    def __getitem__(self, name):
        if isinstance(name, string_types):
            for ix, ch in enumerate(self.channels):
                if name in ch:
                    return self[ix]
        return super(AttocubeScan, self).__getitem__(name)

    @property
    def channels(self):
        if len(self):
            return self.metadata.slice("display", values_only=True)
        else:
            return []

    def _load(self, filename, *args, **kargs):
        """Loads data from a hdf5 file

        Args:
            h5file (string or h5py.Group): Either a string or an h5py Group object to load data from

        Returns:
            itself after having loaded the data
        """
        if filename is None or not filename:
            self.get_filename("r")
            filename = self.filename
        else:
            self.filename = filename
        if isinstance(filename, string_types):  # We got a string, so we'll treat it like a file...
            f = _open_filename(filename)
        elif isinstance(filename, h5py.File) or isinstance(filename, h5py.Group):
            f = filename
        else:
            _raise_error(f, message=f"Couldn't interpret {filename} as a valid HDF5 file or group or filename")
        if "type" not in f.attrs:
            _raise_error(f, message=f"HDF5 Group does not specify the type attribute used to check we can load it.")
        typ = bytes2str(f.attrs["type"])
        if typ != self.__class__.__name__ and "module" not in f.attrs:
            _raise_error(
                f,
                message=f"HDF5 Group is not a {self.__class__.__name__} and does not specify a module to use to load.",
            )
        loader = None
        if typ == self.__class__.__name__:
            loader = getattr(self.__clas__, "read_HDF")
        else:
            mod = importlib.import_module(bytes2str(f.attrs["module"]))
            cls = getattr(mod, typ)
            loader = getattr(cls, "read_JDF")
        if loader is None:
            _raise_error(f, message="Could not et loader for {bytes2str(f.attrs['module'])}.{typ}")

        return loader(f, *args, **kargs)

    def _instantiate(self, idx):
        """Reconstructs the data type."""
        r, c = self._sizes[idx]
        if issubclass(
            self.type, ImageArray
        ):  # IF the underlying type is an ImageArray, then return as a view with extra metadata
            tmp = self._stack[:r, :c, idx].view(type=self.type)
        else:  # Otherwise it must be something with a data attribute
            tmp = self.type()
            tmp.data = self._stack[:r, :c, idx]
        tmp.metadata = deepcopy(self._common_metadata)
        tmp.metadata.update(self._metadata[self.__names__()[idx]])
        tmp.metadata["Scan #"] = self.scan_no
        tmp._fromstack = True
        return tmp

    def _load_files(self, root_name, regrid):
        """Build the image stack from a stack of files.

        Args:
            root_name(str):
                The scan prefix e.g. SC_###
            regrid(bool):
                Use the X and Y positions if available to regrid the data.
        """

        if not path.exists(path.join(self.directory, f"{root_name}-Parameters.txt")):
            return False
        self._load_parameters(root_name)
        for data in glob(path.join(self.directory, f"{root_name}*.asc")):
            if data.endswith("fwd.asc"):
                if "fwd" not in self.groups:
                    self.add_group("fwd")
                target = self.groups["fwd"]
            elif data.endswith("bwd.asc"):
                if "bwd" not in self.groups:
                    self.add_group("bwd")
                target = self.groups["bwd"]
            else:
                target = self
            target._load_asc(data)
        if regrid:
            if "fwd" in self.groups:
                self.groups["fwd"].regrid(in_place=True)
            if "bwd" in self.groups:
                self.groups["bwd"].regrid(in_place=True)
            if not self.groups:
                self.regrid(in_place=True)
        return self

    def _load_parameters(self, root_name):

        filename = path.join(self.directory, f"{root_name}-Parameters.txt")
        with open(filename, "r") as parameters:
            if not parameters.readline().startswith("Daisy Parameter Snapshot"):
                raise IOError("Parameters file exists but does not have correct header")
            for line in parameters:
                if not line.strip():
                    continue
                parts = [x.strip() for x in line.strip().split(":")]
                key = parts[0]
                value = ":".join(parts[1:])
                units = PARAM_RE.match(value)
                if units and units.groups()[1]:
                    key += f" [{units.groups()[1]}]"
                    value = units.groups()[0]
                self._common_metadata[key] = value
        return self

    def _load_asc(self, filename):
        with open(filename, "r") as data:
            if not data.readline().startswith("# Daisy frame view snapshot"):
                raise ValueError(f"{filename} lacked the correct header line")
            tmp = ImageFile()
            for line in data:
                if not line.startswith("# "):
                    break
                parts = [x.strip() for x in line[2:].strip().split(":")]
                key = parts[0]
                value = ":".join(parts[1:])
                units = PARAM_RE.match(value)
                if units and units.groups()[1]:
                    key += f" [{units.groups()[1]}]"
                    value = units.groups()[0]
                tmp.metadata[key] = value
        xpx = tmp["x-pixels"]
        ypx = tmp["y-pixels"]
        metadata = tmp.metadata
        tmp.image = genfromtxt(filename).reshape((xpx, ypx))
        tmp.metadata = metadata
        tmp.filename = tmp["display"]
        self.append(tmp)
        return self

    def _read_signal(self, g):
        """Read a signal array and return a member of the image stack."""
        if "signal" not in g:
            _raise_error(g.parent, message=f"{g.name} does not have a signal dataset !")
        tmp = self.type()  # pylint: disable=E1102
        data = g["signal"]
        if product(array(data.shape)) > 0:
            tmp.image = data[...]
        else:
            tmp.image = [[]]
        metadata = g.require_group("metadata")
        typehints = g.get("typehints", None)
        if not isinstance(typehints, h5py.Group):
            typehints = dict()
        else:
            typehints = typehints.attrs
        for i in sorted(metadata.attrs):
            v = metadata.attrs[i]
            t = typehints.get(i, "Detect")
            if isinstance(v, string_types) and t != "Detect":  # We have typehints and this looks like it got exported
                tmp.metadata["{}{{{}}}".format(i, t).strip()] = "{}".format(v).strip()
            else:
                tmp[i] = metadata.attrs[i]
        tmp.filename = path.basename(g.name)
        return tmp

    def regrid(self, **kargs):
        """Regrid the data sets based on PosX and PosY channels.

        Keyword Parameters:
            x_range, y_range (tuple of start, stop, points):
                Range of x-y co-rdinates to regrid the data to. Used as an argument to :py:func:`np.linspace` to generate the co-ordinate
                vector.
            in_place (bool):
                If True then replace the existing datasets with the regridded data, otherwise create a new copy of the scan object. Default
                is False.

        Returns:
            (AttocubeScan):
                Scan object with regridded data. May be the same as the source object if in_place is True.

        """

        if not kargs.get("in_place", False):
            new = self.clone
        else:
            new = self
        try:
            x = self["PosX"]
            y = self["PosY"]
        except KeyError:  # Can't get X and Y data
            return new

        xrange = kargs.pop("x_range", (x[:, 0].max(), x[:, -1].min(), x.shape[1]))
        yrange = kargs.pop("y_range", (y[0].max(), y[-1].min(), y.shape[0]))
        nX, nY = meshgrid(linspace(*xrange), linspace(*yrange))
        for data in self.channels:
            if "PosX" in data or "PosY" in data:
                continue
            z = self[data]
            nZ = griddata((x.ravel(), y.ravel()), z.ravel(), (nX.ravel(), nY.ravel()), method="cubic").reshape(
                nX.shape
            )
            new[data].data = nZ
        new["PosX"].data = nX
        new["PosY"].data = nY

        return new

    def level_image(self, method="plane", signal="Amp"):
        """Remove a background signla by fitting an appropriate function.

        Keyword Arguments:
            method (str or callable):
                Eirther the name of a fitting function in the global scope, or a callable. *plane* and *parabola* are already defined.
            signal (str):
                The name of the dataset to be flattened. Defaults to the Amplitude signal

        Returns:
            (AttocubeScan):
                The current scan object with the data modified.
        """
        if isinstance(method, string_types):
            method = globals()[method]
        if not callable(method):
            raise ValueError("Could not get a callable method to flatten the data")
        data = self[signal]
        ys, xs = data.shape
        X, Y = meshgrid(linspace(-1, 1, xs), linspace(-1, 1, ys))
        Z = data.data
        X = X.ravel()
        Y = Y.ravel()
        Z = Z.ravel()

        popt = curve_fit(method, (X, Y), Z)[0]

        nZ = method((X, Y), *popt)
        Z -= nZ

        data.data = Z.reshape(xs, ys)
        return self

    def to_HDF5(self, filename=None, **kargs):
        """Save the AttocubeScan to an hdf5 file."""
        if filename is None:
            filename = path.join(self.directory, f"SC_{self.scan_no:03d}.hdf5")
        if filename is None or (isinstance(filename, bool) and not filename):  # now go and ask for one
            filename = self.__file_dialog("w")
            self.filename = filename
        if isinstance(filename, string_types):
            mode = "r+" if path.exists(filename) else "w"
            f = h5py.File(filename, mode)
        elif isinstance(filename, h5py.File) or isinstance(filename, h5py.Group):
            f = filename

        f.attrs["type"] = self.__class__.__name__
        f.attrs["module"] = self.__class__.__module__
        f.attrs["scan_no"] = self.scan_no
        if "common_metadata" in f.parent and "common_metadata" not in f:
            f["common_metadata"] = h5py.SoftLink(f.parent["common_metadata"].name)
            f["common_typehints"] = h5py.SoftLink(f.parent["common_typehints"].name)
        else:
            metadata = f.require_group("common_metadata")
            typehints = f.require_group("common_typehints")
            for k in self._common_metadata:
                try:
                    typehints.attrs[k] = self._common_metadata._typehints[k]
                    metadata.attrs[k] = self._common_metadata[k]
                except TypeError:  # We get this for trying to store a bad data type - fallback to metadata export to string
                    parts = self._common_metadata.export(k).split("=")
                    metadata.attrs[k] = "=".join(parts[1:])

        for g in self.groups:  # Recurse to save groups
            grp = f.require_group(g)
            self.groups[g].to_HDF5(grp)

        for ch in self.channels:
            signal = f.require_group(ch)
            data = self[ch]
            signal.require_dataset(
                "signal",
                data=data.data,
                shape=data.shape,
                dtype=data.dtype,
                compression=self.compression,
                compression_opts=self.compression_opts,
            )
            metadata = signal.require_group("metadata")
            typehints = signal.require_group("typehints")
            for k in [x for x in data.metadata if x not in self._common_metadata]:
                try:
                    typehints.attrs[k] = data.metadata._typehints[k]
                    metadata.attrs[k] = data.metadata[k]
                except TypeError:  # We get this for trying to store a bad data type - fallback to metadata export to string
                    parts = data.metadata.export(k).split("=")
                    metadata.attrs[k] = "=".join(parts[1:])

        if isinstance(f, h5py.File):
            self.filename = f.filename
        elif isinstance(f, h5py.Group):
            self.filename = f.file.filename
        else:
            self.filename = filename
        if isinstance(filename, string_types):
            f.file.close()

        return self

    @classmethod
    def read_HDF(cls, filename, *args, **kargs):
        """Create a new instance from an hdf file."""
        self = cls(regrid=False)
        close_me = False
        if filename is None or not filename:
            self.get_filename("r")
            filename = self.filename
        else:
            self.filename = filename
        if isinstance(filename, string_types):  # We got a string, so we'll treat it like a file...
            f = _open_filename(filename)
            close_me = True
        elif isinstance(filename, h5py.File) or isinstance(filename, h5py.Group):
            f = filename
        else:
            _raise_error(f, message=f"Couldn't interpret {filename} as a valid HDF5 file or group or filename")
        self.scan_no = f.attrs["scan_no"]
        grps = list(f.keys())
        if "common_metadata" not in grps or "common_typehints" not in grps:
            _raise_error(f, message="Couldn;t find common metadata groups, something is not right here!")
        metadata = f["common_metadata"].attrs
        typehints = f["common_typehints"].attrs
        for i in sorted(metadata):
            v = metadata[i]
            t = typehints.get(i, "Detect")
            if isinstance(v, string_types) and t != "Detect":  # We have typehints and this looks like it got exported
                self._common_metadata["{}{{{}}}".format(i, t).strip()] = "{}".format(v).strip()
            else:
                self._common_metadata[i] = metadata[i]
        grps.remove("common_metadata")
        grps.remove("common_typehints")
        for grp in grps:
            if "type" in f[grp].attrs:
                self.groups[grp] = cls.read_HDF(f[grp], *args, **kargs)
                continue
            else:  # This is an actual image!
                g = f[grp]
            self.append(self._read_signal(g))
        if close_me:
            f.close()
        return self
