"""Stoner.Core provides the core classes for the Stoner package."""
__all__ = [
    "StonerLoadError",
    "StonerSetasError",
    "_setas",
    "regexpDict",
    "typeHintedDict",
    "metadataObject",
    "DataArray",
    "DataFile",
]

import re

# import pdb # for debugging
import os
import io
import copy
from collections.abc import MutableSequence
import os.path as path
import inspect as _inspect_
from textwrap import TextWrapper
from traceback import format_exc
import csv

import numpy as np
from numpy import NaN  # NOQA pylint: disable=unused-import
from numpy import ma

from .compat import string_types, int_types, index_types, get_filedialog, str2bytes, _pattern_type
from .tools import all_type, isIterable, isLikeList, get_option

from .core.exceptions import StonerLoadError, StonerSetasError, StonerUnrecognisedFormat
from .core import _setas, regexpDict, typeHintedDict, metadataObject
from .core.array import DataArray
from .core.operators import DataFileOperatorsMixin
from .core.property import DataFilePropertyMixin
from .core.interfaces import DataFileInterfacesMixin
from .core.methods import DataFileSearchMixin
from .core.utils import copy_into, tab_delimited, subclasses

try:
    from tabulate import tabulate

    tabulate.PRESERVE_WHITESPACE = True
except ImportError:
    tabulate = None

try:
    from magic import Magic as filemagic, MAGIC_MIME_TYPE
except ImportError:
    filemagic = None

try:
    import pandas as pd
except ImportError:
    pd = None


class DataFile(
    DataFileSearchMixin,
    DataFileInterfacesMixin,
    DataFileOperatorsMixin,
    DataFilePropertyMixin,
    metadataObject,
    MutableSequence,
):

    """Base class object that represents a matrix of data, associated metadata and column headers.

    Attributes:
        column_headers (list):
            list of strings of the column names of the data.
        data (2D numpy masked array):
            The attribute that stores the nuermical data for each DataFile. This is a :py:class:`DataArray` instance -
            which is itself a subclass of :py:class:`numpy.ma.MaskedArray`.
        title (string):
            The title of the measurement.
        filename (string):
            The current filename of the data if loaded from or already saved to disc. This is the default filename
            used by the :py:meth:`Stoner.Core.DataFile.load` and :py:meth:`Stoner.Core.DataFile.save`.
        header (string):
            A readonly property that returns a pretty formatted string giving the header of tabular representation.
        mask (array of booleans):
            Returns the current mask applied to the numerical data equivalent to self.data.mask.
        mime_type (list of str):
            The possible mime-types of data files represented by each matching filename pattern in
            :py:attr:`Datafile.pattern`.
        patterns (list):
            A list of filename extenion glob patterns that matrches the expected filename patterns for a DataFile
            (*.txt and *.dat")
        priority (int):
            Used to indicathe order in which subclasses of :py:class:`DataFile` are tried when loading data. A higher
            number means a lower priority (!)
        setas (:py:class:`_stas`):
            Defines certain columns to contain X, Y, Z or errors in X,Y,Z data.
        shape (tuple of integers):
            Returns the shape of the data (rows,columns) - equivalent to self.data.shape.
        records (numpy record array):
            Returns the data in the form of a list of yuples where each tuple maps to the columsn names.
        clone (DataFile):
            Creates a deep copy of the :py:class`DataFile` object.
        dict_records (array of dictionaries):
            View the data as an array or dictionaries where each dictionary represnets one row with keys dervied
            from column headers.
        dims (int):
            When data columns are set as x,y,z etc. returns the number of dimensions implied in the data set
        dtype (numpoy dtype):
            Returns the datatype stored in the :py:attr:`DataFile.data` attribute.
        T (:py:class:`DataArray`):
            Transposed version of the data.
        subclasses (list):
            Returns a list of all the subclasses of DataFile currently in memory, sorted by
            their py:attr:`Stoner.Core.DataFile.priority`. Each entry in the list consists of the
            string name of the subclass and the class object.
        xcol (int):
            If a column has been designated as containing *x* values, this will return the index of that column
        xerr (int):
            Similarly to :py:attr:`DataFile.xcol` but for the x-error value column.
        ycol (list of int):
            Similarly to :py:attr:`DataFile.xcol` but for the y value columns.
        yerr (list of int):
            Similarly to :py:attr:`DataFile.xcol` but for the y error value columns.
        zcol (list of int):
            Similarly to :py:attr:`DataFile.xcol` but for the z value columns.
        zerr (list of int):
            Similarly to :py:attr:`DataFile.xcol` but for the z error value columns.
        ucol (list of int):
            Similarly to :py:attr:`DataFile.xcol` but for the u (x-axis direction cosine) columns.
        vcol (list of int):
            Similarly to :py:attr:`DataFile.xcol` but for the v (y-axis direction cosine) columns.
        wcol (list of int):
            Similarly to :py:attr:`DataFile.xcol` but for the w (z-axis direction cosine) columns.
    """

    #: priority (int): is the load order for the class, smaller numbers are tried before larger numbers.
    #   .. note::
    #
    #      Subclasses with priority<=32 should make some positive identification that they have the right
    #      file type before attempting to read data.
    priority = 32

    #: pattern (list of str): A list of file extensions that might contain this type of file. Used to construct
    # the file load/save dialog boxes.
    _patterns = ["*.txt", "*.tdi"]  # Recognised filename patterns

    # mimetypes we match
    mime_type = ["text/plain"]

    _conv_string = np.vectorize(str)
    _conv_float = np.vectorize(float)

    # ====================================================================================
    ############################     Object Construction   ###############################
    # ====================================================================================

    def __new__(cls, *args, **kargs):
        """Prepare the basic DataFile instance before the mixins add their bits."""
        self = metadataObject.__new__(cls)
        object.__setattr__(self, "debug", kargs.pop("debug", False))
        self._masks = [False]
        self._filename = None
        self._public_attrs_real = {}
        object.__setattr__(self, "_data", DataArray([]))
        self._baseclass = DataFile
        self._kargs = kargs
        return self

    def __init__(self, *args, **kargs):
        """Initialise the DataFile from arrays, dictionaries and filenames.

        Various forms are recognised:

        .. py:function:: DataFile('filename',<optional filetype>,<args>)
            :noindex:

            Creates the new DataFile object and then executes the :py:class:`DataFile`.load
            method to load data from the given *filename*.

        .. py:function:: DataFile(array)
            :noindex:

            Creates a new DataFile object and assigns the *array* to the
            :py:attr:`DataFile.data`  attribute.

        .. py:function:: DataFile(dictionary)
            :noindex:

            Creates the new DataFile object. If the dictionary keys are all strigns and the values are all
            numpy D arrays of equal length, then assumes the dictionary represents columns of data and the keys
            are the column titles, otherwise initialises the metadata with :parameter: dictionary.

        .. py:function:: DataFile(array,dictionary)
            :noindex:

            Creates the new DataFile object and does the combination of the
            previous two forms.


        .. py:function:: DataFile(DataFile)
            :noindex:

            Creates the new DataFile object and initialises all data from the
            existing :py:class:`DataFile` instance. This on the face of it does the same as
            the assignment operator, but is more useful when one or other of the
            DataFile objects is an instance of a sub - class of DataFile

        Args:
            args (positional arguments):
                Variable number of arguments that match one of the definitions above
            kargs (keyword Arguments):
                All keyword arguments that match public attributes are used to set those public attributes.
        """
        # init instance attributes
        super(DataFile, self).__init__(*args, **kargs)  # initialise self.metadata)
        self._public_attrs = {
            "data": np.ndarray,
            "setas": (string_types, list, dict),
            "column_headers": list,
            "metadata": typeHintedDict,
            "debug": bool,
            "filename": string_types,
            "mask": (np.ndarray, bool),
        }
        self._repr_limits = (256, 6)
        i = len(args) if len(args) < 3 else 3
        handler = [None, self._init_single, self._init_double, self._init_many][i]
        self.mask = False
        self.data._setas._get_cols()
        if handler is not None:
            handler(*args, **kargs)
        try:
            kargs = self._kargs
            delattr(self, "_kargs")
        except AttributeError:
            pass
        self.metadata["Stoner.class"] = self.__class__.__name__
        if kargs:  # set public attributes from keywords
            to_go = []
            for k in kargs:
                if k in self._public_attrs:
                    if isinstance(kargs[k], self._public_attrs[k]):
                        self.__setattr__(k, kargs[k])
                    else:
                        self._raise_type_error(k)
                        to_go.append(k)
                else:
                    raise AttributeError(f"{k} is not a recognised attribute ({list(self._public_attrs.keys())})")
            for k in to_go:
                del kargs[k]
        if self.debug:
            print("Done DataFile init")

    def _init_single(self, *args, **kargs):
        """Handle constructor with 1 arguement - called from __init__."""
        arg = args[0]
        if isinstance(arg, string_types) or (isinstance(arg, bool) and not arg) or isinstance(arg, io.IOBase):
            # Filename- load datafile
            self.load(filename=arg, **kargs)
        elif isinstance(arg, np.ndarray):
            # numpy.array - set data
            if np.issubdtype(arg.dtype, np.number):
                self.data = DataArray(np.atleast_2d(arg), setas=self.data._setas)
                self.column_headers = [f"Column_{x}" for x in range(np.shape(args[0])[1])]
            elif isinstance(arg[0], dict):
                for row in arg:
                    self += row
        elif isinstance(arg, dict):  # Dictionary - use as data or metadata
            if (
                all_type(arg.keys(), string_types)
                and all_type(arg.values(), np.ndarray)
                and np.all([len(arg[k].shape) == 1 and np.all(len(arg[k]) == len(list(arg.values())[0])) for k in arg])
            ):
                self.data = np.column_stack(tuple(arg.values()))
                self.column_headers = list(arg.keys())
            else:
                self.metadata = arg.copy()
        elif isinstance(arg, DataFile):
            for a in arg.__dict__:
                if not callable(a) and a != "_baseclass":
                    super(DataFile, self).__setattr__(a, copy.copy(arg.__getattribute__(a)))
            self.metadata = arg.metadata.copy()
            self.data = DataArray(arg.data, setas=arg.setas.clone)
            self.data.setas = arg.setas.clone
        elif "<class 'Stoner.Image.core.ImageFile'>" in [
            str(x) for x in arg.__class__.__mro__
        ]:  # Crazy hack to avoid importing a circular ref!
            x = arg.get("x_vector", np.arange(arg.shape[1]))
            y = arg.get("y_vector", np.arange(arg.shape[0]))
            x, y = np.meshgrid(x, y)
            z = arg.image

            self.data = np.column_stack((x.ravel(), y.ravel(), z.ravel()))
            self.metadata = copy.deepcopy(arg.metadata)
            self.column_headers = ["X", "Y", "Image Intensity"]
            self.setas = "xyz"
        elif pd is not None and isinstance(arg, pd.DataFrame):
            self.data = arg.values
            ch = []
            for ix, col in enumerate(arg):
                if isinstance(col, string_types):
                    ch.append(col)
                elif isIterable(ch):
                    for ch_i in col:
                        if isinstance(ch_i, string_types):
                            ch.append(ch_i)
                            break
                    else:
                        ch.append(f"Column {ix}")
                else:
                    ch.append("Column {}:{}", format(ix, ch))
            self.column_headers = ch
            self.metadata.update(arg.metadata)
            if isinstance(arg.columns, pd.MultiIndex) and len(arg.columns.levels) > 1:
                for label in arg.columns.get_level_values(1):
                    if label not in list("xyzdefuvw."):
                        break
                else:
                    self.setas = list(arg.columns.get_level_values(1))
        elif isIterable(arg) and all_type(arg, string_types):
            self.column_headers = list(arg)
        elif isIterable(arg) and all_type(arg, np.ndarray):
            self._init_many(*arg, **kargs)
        else:
            raise SyntaxError(f"No constructor for {type(arg)}")
        self.data._setas.cols.update(self.setas._get_cols())

    def _init_double(self, *args, **kargs):
        """Two argument constructors handled here. Called form __init__."""
        (arg0, arg1) = args
        if isinstance(arg1, dict) or (isIterable(arg1) and all_type(arg1, string_types)):
            self._init_single(arg0, **kargs)
            self._init_single(arg1, **kargs)
        elif (
            isinstance(arg0, np.ndarray)
            and isinstance(arg1, np.ndarray)
            and len(arg0.shape) == 1
            and len(arg1.shape) == 1
        ):
            self._init_many(*args, **kargs)

    def _init_many(self, *args, **kargs):
        """Handle more than two arguments to the constructor - called from init."""
        for a in args:
            if not (isinstance(a, np.ndarray) and len(a.shape) == 1):
                self.load(*args, **kargs)
                break
        else:
            self.data = np.column_stack(args)

    # ============================================================================================
    ############################   Speical Methods ###############################################
    # ============================================================================================

    def __call__(self, *args, **kargs):
        """Clone the DataFile, but allowing additional arguments to modify the new clone.

        Args:
            *args (tuple):
                Positional arguments to pass through to the new clone.
            **kargs (dict):
                Keyword arguments to pass through to the new clone.

        Raises:
            TypeError: If a keyword argument doesn't match an attribute.

        Returns:
            new_d (DataFile):
                Modified clone of the current object.
        """
        new_d = self.clone
        i = len(args) if len(args) < 2 else 2
        handler = [None, new_d._init_single, new_d._init_double, new_d._init_many][i]
        if handler is not None:
            handler(*args, **kargs)
        if kargs:  # set public attributes from keywords
            myattrs = new_d._public_attrs
            for k in kargs:
                if k in myattrs:
                    if isinstance(kargs[k], myattrs[k]):
                        new_d.__setattr__(k, kargs[k])
                    else:
                        if isinstance(myattrs[k], tuple):
                            typ = "one of " + ",".join([str(type(t)) for t in myattrs[k]])
                        else:
                            typ = f"a {type(myattrs[k])}"
                        raise TypeError(f"{k} should be {typ} not a {type(kargs[k])}")

        return new_d

    def __deepcopy__(self, memo):
        """Provide support for copy.deepcopy to work."""
        cls = self.__class__
        result = cls.__new__(cls)
        memo[id(self)] = result
        for k, v in self.__dict__.items():
            try:
                setattr(result, k, copy.deepcopy(v, memo))
            except Exception:
                setattr(result, k, copy.copy(v))
        return result

    def __dir__(self):
        """Reeturns the attributes of the current object.

        Augmenting the keys of self.__dict__ with the attributes that __getattr__ will handle."""
        attr = dir(type(self))
        col_check = {"xcol": "x", "xerr": "d", "ycol": "y", "yerr": "e", "zcol": "z", "zerr": "f"}
        for k in col_check:
            if "_setas" not in self.__dict__:
                break
            if k.startswith("x"):
                if k in self._data._setas.cols and self._data._setas.cols[k] is not None:
                    attr.append(col_check[k])
            else:
                if k in self._data._setas.cols and self._data._setas.cols[k]:
                    attr.append(col_check[k])
        return sorted(set(attr))

    def __getattr__(self, name):
        """Handle some special pseudo attributes that map to the setas columns.

        Args:
            name (string):
                The name of the attribute to be returned.

        Returns:
            Various:
                the DataFile object in various forms

        Supported attributes:
            - records:
                return the DataFile data as a numpy structured
                array - i.e. rows of elements whose keys are column headings
                - clone:
                    returns a deep copy of the current DataFile instance

        Otherwise the name parameter is tried as an argument to :py:meth:`DataFile.column` and the resultant column
        is returned. If DataFile.column raises a KeyError this is remapped as an AttributeError.
        """
        setas_cols = ("x", "y", "z", "d", "e", "f", "u", "v", "w", "r", "q", "p")
        if name != "debug" and self.debug:
            print(name)
        try:
            return super(DataFile, self).__getattr__(name)
        except AttributeError:
            ret = self.__dict__.get(name, self.__class__.__dict__.get(name, None))
            if ret is not None:
                return ret
        if name in setas_cols:
            ret = self._getattr_col(name)
            if ret is not None:
                return ret
        if name in self.setas.cols:
            ret = self.setas.cols[name]
            if ret is not None and ret != []:
                return ret
        try:
            col = self._data._setas.find_col(name)
            return self.column(col)
        except (KeyError, IndexError):
            pass
        if name in setas_cols:  # Probably tried to use a setas col when it wasn't defined
            raise StonerSetasError(
                f"Tried accessing a {name} column, but setas is not defined and {name} is not a column name either"
            )
        raise AttributeError(f"{name} is not an attribute of DataFile nor a column name")

    #    def __reduce_ex__(self, p):
    #        """Machinery used for deepcopy."""
    #        cls=self.__class__
    #        return (cls, (), self.__getstate__())

    def __repr__(self):
        """Output the :py:class:`DataFile` object in TDI format.

        This allows one to print any :py:class:`DataFile` to a stream based
        object andgenerate a reasonable textual representation of the data.shape

        Returns:
            self in a textual format.
        """
        if get_option("short_repr") or get_option("short_data_repr"):
            return self._repr_short_()
        try:
            return self._repr_table_()
        except Exception:
            return self.__repr_core__(256)

    def __setattr__(self, name, value):
        """Handle attempts to set attributes not covered with class attribute variables.

        Args:
            name (str):
                Name of attribute to set. Details of possible attributes below:
                -   mask Passes through to the mask attribute of self.data (which is a numpy masked array).
                    Also handles the case where you pass a callable object to nask where we pass each row to the
                    function and use the return reult as the mask
                -   data Ensures that the :py:attr:`data` attribute is always a :py:class:`numpy.ma.maskedarray`
        """
        if hasattr(type(self), name) and isinstance(getattr(type(self), name), property):
            super(DataFile, self).__setattr__(name, value)
        elif len(name) == 1 and name in "xyzuvwdef" and self.setas[name]:
            self._setattr_col(name, value)
        else:
            super(DataFile, self).__setattr__(name, value)

    def __str__(self):
        """Provide an implementation for str(DataFile) that does not shorten the output."""
        return self.__repr_core__(False)

    # ============================================================================================
    ############################ Private Methods #################################################
    # ============================================================================================

    def _col_args(self, *args, **kargs):
        """Create an object which has keys  based either on arguments or setas attribute."""
        return self.data._col_args(*args, **kargs)  # Now just pass through to DataArray

    def __file_dialog(self, mode):
        """Create a file dialog box for loading or saving ~b DataFile objects.

        Args:
            mode (string):
                The mode of the file operation  'r' or 'w'

        Returns:
            (str):
                A filename to be used for the file operation.
        """
        # Wildcard pattern to be used in file dialogs.

        descs = {"*.*": "All Files"}
        patterns = self.patterns
        for p in patterns:  # pylint: disable=not-an-iterable
            descs[p] = self.__class__.__name__ + " file"
        for c in subclasses(DataFile):  # pylint: disable=E1136, E1133
            for p in subclasses(DataFile)[c].patterns:  # pylint: disable=unsubscriptable-object
                if p in descs:
                    descs[p] += (
                        ", " + subclasses(DataFile)[c].__name__ + " file"  # pylint: disable=E1136
                    )  # pylint: disable=unsubscriptable-object
                else:
                    descs[p] = subclasses(DataFile)[c].__name__ + " file"  # pylint: disable=unsubscriptable-object

        patterns = descs

        if self.filename is not None:
            filename = os.path.basename(self.filename)
            dirname = os.path.dirname(self.filename)
        else:
            filename = ""
            dirname = ""
        if "r" in mode:
            mode = "file"
        elif "w" in mode:
            mode = "save"
        else:
            mode = "directory"
        dlg = get_filedialog(what=mode, start=dirname, patterns=patterns)
        if dlg:
            self.filename = dlg
            return self.filename
        return None

    def _getattr_col(self, name):
        """Get a column using the setas attribute."""
        try:
            return self._data.__getattr__(name)
        except StonerSetasError:
            return None

    def _interesting_cols(self, cols):
        """Workout which columns the user might be interested in in the basis of the setas.

        ArgsL
            cols (float):
                Maximum Number of columns to display

        Returns
            list(ints):
                The indices of interesting columns with breaks in runs indicated by -1
        """
        c = self.shape[1]
        if c > cols:
            interesting = []
            last = -1
            for ix, typ in enumerate(self.setas):
                if last != -1 and last != ix - 1:
                    interesting.append(-1)
                    last = -1
                if typ != ".":
                    interesting.append(ix)
                    last = ix
            if interesting and interesting[-1] == -1:
                interesting = interesting[:-1]
            if interesting:
                c_start = max(interesting) + 1
            else:
                c_start = 0
            interesting.extend(range(c_start, c))
            if len(interesting) < cols:
                cols = len(interesting)
            if interesting[cols - 3] != -1:
                interesting[cols - 2] = -1
            else:
                interesting[cols - 2] = c - 2
            interesting[cols - 1] = c - 1
            interesting = interesting[:cols]
            c = cols
        else:
            interesting = list(range(c))

        col_assignments = []
        for i in interesting:
            if i != -1:
                if self.setas[i] != ".":
                    col_assignments.append(f"{i} ({self.setas[i]})")
                else:
                    col_assignments.append(f"{i}")
            else:
                col_assignments.append("")
        return interesting, col_assignments, cols

    def _load(self, filename, *args, **kargs):
        """Actually load the data from disc assuming a .tdi file format.

        Args:
            filename (str):
                Path to filename to be loaded. If None or False, a dialog bax is raised to ask for the filename.

        Returns:
            DataFile:
                A copy of the newly loaded :py:class`DataFile` object.

        Exceptions:
            StonerLoadError:
                Raised if the first row does not start with 'TDI Format 1.5' or 'TDI Format=1.0'.

        Note:
            The *_load* methods shouldbe overidden in each child class to handle the process of loading data from
            disc. If they encounter unexpected data, then they should raise StonerLoadError to signal this, so that
            the loading class can try a different sub-class instead.
        """
        if filename is None or not filename:
            self.get_filename("r")
        else:
            self.filename = filename
        with io.open(self.filename, "r", encoding="utf-8", errors="ignore") as datafile:
            try:
                reader = csv.reader(datafile, dialect=tab_delimited())
                row = next(reader)
                if row[0].strip() == "TDI Format 1.5":
                    fmt = 1.5
                elif row[0].strip() == "TDI Format=Text 1.0":
                    fmt = 1.0
                else:
                    raise StonerLoadError("Not a TDI File")
            except Exception:
                raise StonerLoadError("Not a TDI File")
            col_headers_tmp = [x.strip() for x in row[1:]]
            data_array = 0
            metaDataArray = 0
            cols = 0
            for row in reader:  # Now read through the metadata columns
                if len(row) > 1 and row[1].strip() != "":
                    data_array += 1
                    still_data = True
                else:
                    still_data = False
                if row[0].strip() == "":  # end of metadata:
                    break
                else:
                    cols = max(cols, len(row))
                    metaDataArray += 1
                    self.metadata.import_key(row[0])
        # End of metadata reading, close filke and reopen to read data
        if still_data:  # data extends beyond metada - read with genfromtxt
            self.data = DataArray(
                np.genfromtxt(
                    self.filename,
                    skip_header=1,
                    usemask=True,
                    delimiter="\t",
                    usecols=range(1, cols),
                    invalid_raise=False,
                    comments="\0",
                )
            )
        elif data_array > 0:  # some data less than metadata
            footer = metaDataArray - data_array
            self.data = DataArray(
                np.genfromtxt(
                    self.filename,
                    skip_header=1,
                    skip_footer=footer,
                    usemask=True,
                    delimiter="\t",
                    comments="\0",
                    usecols=range(1, cols),
                )
            )
        else:
            self.data = np.atleast_2d(np.array([]))
        if len(self.data.shape) >= 2 and self.data.shape[1] > 0:
            self.column_headers = col_headers_tmp
        self["TDI Format"] = fmt
        return self

    # def _parse_metadata(self, key, value):
    #     """Parse the metadata string, removing the type hints into a separate dictionary from the metadata.

    #     Args:
    #         key (string):
    #             The name of the metadata parameter to be written, possibly including a type hinting string.
    #         value (any):
    #             The value of the item of metadata.

    #     Note:
    #         Uses the typehint to set the type correctly in the dictionary

    #         All the clever work of managing the typehinting is done in the metadata dictionary object now.
    #     """
    #     self.metadata[key] = value

    def __repr_core__(self, shorten=1000):
        """Actuall do the repr work, but allow for a shorten parameter to save printing big files out to disc."""
        outp = "TDI Format 1.5\t" + "\t".join(self.column_headers) + "\n"
        m = len(self.metadata)
        self.data = np.atleast_2d(self.data)
        r = np.shape(self.data)[0]
        md = self.metadata.export_all()
        for x in range(min(r, m)):
            if self.data.ndim != 2 or self.shape[1] == 1:
                outp += f"{md[x]}\t{self.data[x]}\n"
            else:
                outp = outp + md[x] + "\t" + "\t".join([str(y) for y in self.data[x].filled()]) + "\n"
        if m > r:  # More metadata
            for x in range(r, m):
                outp = outp + md[x] + "\n"
        elif r > m:  # More data than metadata
            if shorten is not None and shorten and r - m > shorten:
                for x in range(m, m + shorten - 100):
                    if self.data.ndim != 2 or self.shape[1] == 1:
                        outp += "\t" + f"\t{self.data[x]}\n"
                    else:
                        outp += "\t" + "\t".join([str(y) for y in self.data[x].filled()]) + "\n"
                outp += f"... {r - m - shorten + 100} lines skipped...\n"
                for x in range(-100, -1):
                    if self.data.ndim != 2 or self.shape[1] == 1:
                        outp += f"\t\t{self.data[x]}\n"
                    else:
                        outp += "\t" + "\t".join([str(y) for y in self.data[x].filled()]) + "\n"
            else:
                for x in range(m, r):
                    if self.data.ndim != 2 or self.shape[1] == 1:
                        outp += f"\t\t{self.data[x]}\n"
                    else:
                        outp = outp + "\t" + "\t".join([str(y) for y in self.data[x].filled()]) + "\n"
        return outp

    def _repr_html_private(self):
        """Version of repr_core that does and html output."""
        return self._repr_table_("html")

    def _repr_short_(self):
        ret = (
            f"{self.filename}({type(self)}) of shape {self.shape} ({''.join(self.setas)})"
            + f" and {len(self.metadata)} items of metadata"
        )
        return ret

    def _repr_table_(self, fmt="rst"):
        """Convert the DataFile to a 2D array and then feed to tabulate."""
        if tabulate is None:
            raise ImportError("No tabulate.")
        lb = "<br/>" if fmt == "html" else "\n"
        rows, cols = self._repr_limits
        r, c = self.shape
        interesting, col_assignments, cols = self._interesting_cols(cols)
        c = min(c, cols)
        if len(interesting) > 0:
            c_w = max([len(self.column_headers[x]) for x in interesting if x > -1])
        else:
            c_w = 0
        wrapper = TextWrapper(subsequent_indent="\t", width=max(20, max(20, (80 - c_w * c))))
        if r > rows:
            shorten = [True, False]
            r = rows + rows % 2
        else:
            shorten = [False, False]

        shorten[1] = c > cols
        r = max(len(self.metadata), r)

        outp = np.zeros((r + 1, c + 1), dtype=object)
        outp[:, :] = "..."
        ch = [self.column_headers[ix] if ix >= 0 else "...." for ix in interesting]

        for ix, (h, i) in enumerate(zip(ch, col_assignments)):
            spaces1 = " " * ((c_w - len(h)) // 2)
            spaces2 = " " * ((c_w - len(i)) // 2)
            ch[ix] = f"{spaces1}{h}{lb}{spaces2}{i}"
            if self.debug:
                print(len(spaces1), len(spaces2))
        outp[0, 1:] = ch
        outp[1, 1:] = col_assignments
        outp[0, 0] = f"TDI Format 1.5{lb}index"
        i = 1
        for md in self.metadata.export_all():
            md = md.replace("=", "= ")
            for line in wrapper.wrap(md):
                if i >= outp.shape[0]:  # pylint: disable=E1136
                    outp = np.append(outp, [[""] * outp.shape[1]], axis=0)  # pylint: disable=E1136
                outp[i, 0] = line
                i += 1
        for ic, c in enumerate(interesting):
            if c >= 0:
                if shorten[0]:

                    col_out = np.where(self.mask[: r // 2 - 1, c], "#####", self.data[: r // 2 - 1, c].astype(str))
                    outp[1 : r // 2, ic + 1] = col_out
                    col_out = np.where(self.mask[-r // 2 :, c], "#####", self.data[-r // 2 :, c].astype(str))
                    outp[r // 2 + 1 : r + 1, ic + 1] = col_out
                else:
                    col_out = np.where(self.mask[:, c], "#####", self.data[:, c].astype(str))
                    outp[1 : len(self.data) + 1, ic + 1] = col_out
        return tabulate(outp[1:], outp[0], tablefmt=fmt, numalign="decimal", stralign="left")

    def _setattr_col(self, name, value):
        """Attempt to either assign data columns if set up, or setas setting.

        Args:
            name (length 1 string):
                Column type to work with (one of x,y,z,u,v,w,d,e or f)
            value (nd array or column index):
                If an ndarray and the column type corresponding to *name* is set up, then overwrite the column(s)
                of data with this new data. If an index type, then set the corresponding setas assignment to
                these columns.
        """
        if isinstance(value, np.ndarray):
            value = np.atleast_2d(value)
            if value.shape[0] == self.data.shape[0]:
                pass
            elif value.shape[1] == self.data.shape[0]:
                value = value.T
            else:
                raise RuntimeError("Value to be assigned to data columns is the wrong shape!")
            for i, ix in enumerate(self.find_col(self.setas[name], force_list=True)):
                self.data[:, ix] = value[:, i]
        elif isinstance(value, index_types):
            self._set_setas({name: value})

    def _set_mask(self, func, invert=False, cumulative=False, col=0):
        """Apply func to each row in self.data and uses the result to set the mask for the row.

        Args:
            func (callable):
                A Callable object of the form lambda x:True where x is a row of data (numpy
            invert (bool):
                Optionally invert te reult of the func test so that it unmasks data instead
            cumulative (bool):
                if tru, then an unmask value doesn't unmask the data, it just leaves it as it is.
        """
        i = -1
        args = len(_inspect_.getargs(func.__code__)[0])
        for r in self.rows():
            i += 1
            if args == 2:
                t = func(r[col], r)
            else:
                t = func(r)
            if isinstance(t, (bool, np.bool_)):
                if t ^ invert:
                    self.data[i] = ma.masked
                elif not cumulative:
                    self.data[i] = self._data.data[i]
            else:
                for j in range(min(len(t), np.shape(self.data)[1])):
                    if t[j] ^ invert:
                        self.data[i, j] = ma.masked
                    elif not cumulative:
                        self.data[i, j] = self.data.data[i, j]

    def _push_mask(self, mask=None):
        """Copy the current data mask to a temporary store and replace it with a new mask if supplied.

        Args:
            mask (:py:class:numpy.array of bool or bool or None):
                The new data mask to apply (defaults to None = unmask the data

        Returns:
            Nothing
        """
        self._masks.append(self.mask)
        if mask is None:
            self.data.mask = False
        else:
            self.mask = mask

    def _pop_mask(self):
        """Replace the mask on the data with the last one stored by _push_mask().

        Returns:
            Nothing
        """
        self.mask = False
        self.mask = self._masks.pop()  # pylint: disable=E0203
        if not self._masks:  # pylint: disable=E0203
            self._masks = [False]

    def _raise_type_error(self, k):
        """Raise a type error when setting an attribute k."""
        if isinstance(self._public_attrs[k], tuple):
            typ = "one of " + ",".join([str(type(t)) for t in self._public_attrs[k]])
        else:
            typ = f"a {type(self._public_attrs[k])}"
        raise TypeError(f"{k} should be {typ}")

    # ============================================================================================
    ############################   Public Methods ################################################
    # ============================================================================================

    def add_column(self, column_data, header=None, index=None, func_args=None, replace=False, setas=None):
        """Append a column of data or inserts a column to a datafile instance.

        Args:
            column_data (:py:class:`numpy.array` or list or callable):
                Data to append or insert or a callable function that will generate new data

        Keyword Arguments:
            header (string):
                The text to set the column header to,
                if not supplied then defaults to 'col#'
            index (index type):
                The  index (numeric or string) to insert (or replace) the data
            func_args (dict):
                If column_data is a callable object, then this argument
                can be used to supply a dictionary of function arguments to the callable object.
            replace (bool):
                Replace the data or insert the data (default)
            setas (str):
                Set the type of column (x,y,z data etc - see :py:attr:`Stoner.Core.DataFile.setas`)

        Returns:
            self:
                The :py:class:`DataFile` instance with the additonal column inserted.

        Note:
            Like most :py:class:`DataFile` methods, this method operates in-place in that it also modifies
            the original DataFile Instance as well as returning it.
        """
        if index is None or isinstance(index, bool) and index:  # Enure index is set
            index = self.shape[1]
            replace = False
        elif isinstance(index, int_types) and index == self.shape[1]:
            replace = False
        else:
            index = self.find_col(index)

        # Sort out the data and get it into an array of values.
        if isinstance(column_data, list):
            column_data = np.array(column_data)

        if isinstance(column_data, DataArray) and header is None:
            header = column_data.column_headers

        if isinstance(column_data, np.ndarray):
            np_data = column_data
        elif callable(column_data):
            if isinstance(func_args, dict):
                new_data = [column_data(x, **func_args) for x in self]
            else:
                new_data = [column_data(x) for x in self]
            np_data = np.array(new_data)
        else:
            return NotImplemented

        # Sort out the sizes of the arrays
        if np_data.ndim == 1:
            np_data = np.atleast_2d(np_data).T
        cl, cw = np_data.shape

        # Make setas
        setas = "." * cw if setas is None else setas

        if isIterable(setas) and len(setas) == cw:
            for s in setas:
                if s not in ".-xyzuvwdefpqr":
                    raise TypeError(
                        f"setas parameter should be a string or list of letter in the set xyzdefuvw.-, not {setas}"
                    )
        else:
            raise TypeError(
                f"""setas parameter should be a string or list of letter the same length as the number of columns
                being added in the set xyzdefuvw.-, not {setas}"""
            )

        # Make sure our current data is at least 2D and get its size
        if len(self.data.shape) == 1:
            self.data = np.atleast_2d(self.data).T
        if len(self.data.shape) == 2:
            (dr, dc) = self.data.shape
        elif not self.data.shape:
            self.data = np.array([[]])
            (dr, dc) = (0, 0)

        # Expand either our current data or new data to have the same number of rows
        if cl > dr and dc * dr > 0:  # Existing data is finite and too short
            self.data = DataArray(np.append(self.data, np.zeros((cl - dr, dc)), 0), setas=self.setas.clone)
        elif cl < dr:  # New data is too short
            np_data = np.append(np_data, np.zeros((dr - cl, cw)))
            if np_data.ndim == 1:
                np_data = np.atleast_2d(np_data).T
        elif dc == 0:  # Existing data has no width - replace with cl,0
            self.data = DataArray(np.zeros((cl, 0)))
        elif dr == 0:  # Existing data has no rows - expand existing data with zeros to have right length
            self.data = DataArray(np.append(self.data, np.zeros((cl, dr)), axis=0), setas=self.setas.clone)

        # If not replacing, then add extra columns to existing data.
        if not replace:
            colums = copy.copy(self.column_headers)
            old_setas = self.setas.clone
            if index == self.data.shape[1]:  # appending column
                self.data = DataArray(np.append(self.data, np_data, axis=1), setas=self.setas.clone)
            else:
                self.data = DataArray(
                    np.append(
                        self.data[:, :index], np.append(np.zeros_like(np_data), self.data[:, index:], axis=1), axis=1
                    ),
                    setas=self.setas.clone,
                )
            for ix in range(0, index):
                self.column_headers[ix] = colums[ix]
                self.setas[ix] = old_setas[ix]
            for ix in range(index, dc):
                self.column_headers[ix + cw] = colums[ix]
                self.setas[ix + cw] = old_setas[ix]
        # Check that we don't need to expand to overwrite with the new data
        if index + cw > self.shape[1]:
            self.data = DataArray(
                np.append(self.data, np.zeros((self.data.shape[0], self.data.shape[1] - index + cw)), axis=1),
                setas=self.setas.clone,
            )

        # Put the data into the array
        self.data[:, index : index + cw] = np_data

        if header is None:  # This will fix the header if not defined.
            header = [f"Column {ix}" for ix in range(index, index + cw)]
        if isinstance(header, string_types):
            header = [header]
        if len(header) != cw:
            header.extend(["Column {ix}" for x in range(index, index + cw)])
        for ix, (hdr, s) in enumerate(zip(header, setas)):
            self.column_headers[ix + index] = hdr
            self.setas[index + ix] = s

        return self

    def columns(self, not_masked=False, reset=False):
        """Iterate over the columns of data int he datafile.

        Keyword Args:
            no_masked (bool):
                Only iterate over columns that don't have masked elements
            reset (bool):
                If true then reset the iterator (immediately stops the current iteration without returning any data)./

        Yields:
            1D array: Returns the next column of data.
        """
        for ix, col in enumerate(self.data.T):
            if not_masked and ma.is_masked(col):
                continue
            if reset:
                return
            else:
                yield self.column(ix)

    def del_column(self, col=None, duplicates=False):
        """Delete a column from the current :py:class:`DataFile` object.

        Args:
            col (int, string, iterable of booleans, list or re):
                is the column index as defined for :py:meth:`DataFile.find_col` to the column to be deleted

        Keyword Arguments:
            duplicates (bool):
                (default False) look for duplicated columns

        Returns:
            self:
                The :py:class:`DataFile` object with the column deleted.

        Note:
            - If duplicates is True and col is None then all duplicate columns are removed,
            - if col is not None and duplicates is True then all duplicates of the specified column are removed.
            - If duplicates is False and *col* is either None or False then all masked coplumns are deleeted. If
                *col* is True, then all columns that are not set i the :py:attr:`setas` attrobute are delted.
            - If col is a list (duplicates should not be None) then the all the matching columns are found.
            - If col is an iterable of booleans, then all columns whose elements are False are deleted.
            - If col is None and duplicates is None, then all columns with at least one elelemtn masked
                    will be deleted
        """
        if duplicates:
            ch = self.column_headers
            dups = []
            if col is None:
                for i, chi in enumerate(ch):
                    if chi in ch[i + 1 :]:
                        dups.append(ch.index(chi, i + 1))
            else:
                col = ch[self.find_col(col)]
                i = ch.index(col)
                while True:
                    try:
                        i = ch.index(col, i + 1)
                        dups.append(i)
                    except ValueError:
                        break
            return self.del_column(dups, duplicates=False)
        if col is None or (isinstance(col, bool) and not col):  # Without defining col we just compress by the mask
            self.data = ma.mask_cols(self.data)
            t = DataArray(self.column_headers)
            t.mask = self.mask[0]
            self.column_headers = list(ma.compressed(t))
            self.data = ma.compress_cols(self.data)
        elif isinstance(col, bool) and col:  # Without defining col we just compress by the mask
            ch = [self.column_headers[ix] for ix, v in enumerate(self.setas.set) if v]
            setas = [self.setas[ix] for ix, v in enumerate(self.setas.set) if v]
            self.data = self.data[:, self.setas.set]
            self.setas = setas
            self.column_headers = ch
        elif isIterable(col) and all_type(col, bool):  # If col is an iterable of booleans then we index by that
            col = ~np.array(col)
            new_setas = np.array(self.setas)[col]
            new_column_headers = np.array(self.column_headers)[col]
            self.data = self.data[:, col]
            self.setas = new_setas
            self.column_headers = new_column_headers
        else:  # Otherwise find individual columns
            c = self.find_col(col)
            ch = self.column_headers
            self.data = DataArray(np.delete(self.data, c, 1), mask=np.delete(self.data.mask, c, 1))
            if isinstance(c, list):
                c.sort(reverse=True)
            else:
                c = [c]
            for cl in c:
                del ch[cl]
            self.column_headers = ch
        return self

    def del_nan(self, col=None, clone=False):
        """Remove rows that have nan in them.

        eyword Arguments:
            col (index types or None):
                column(s) to look for nan's in. If None or not given, use setas columns.
            clone (boolean):
                if True clone the current object before running and then return the clone not self.

        Return:
            self (DataFile):
                Returns a copy of the current object (or clone if *clone*=True)
        """
        if clone:  # Set ret to be our clone
            ret = self.clone
        else:  # Not cloning so ret is self
            ret = self

        if col is None:  # If col is still None, use all columsn that are set to any value in self.setas
            col = [ix for ix, col in enumerate(self.setas) if col != "."]
        if not isLikeList(col):  # If col isn't a list, make it one now
            col = [col]
        col = [ret.find_col(c) for c in col]  # Normalise col to be a list of integers
        dels = np.zeros(len(ret)).astype(bool)
        for ix in col:
            dels = np.logical_or(
                dels, np.isnan(ret.data[:, ix])
            )  # dels contains True if any row contains a NaN in columns col
        not_masked = np.logical_not(ma.mask_rows(ret.data).mask[:, 0])  # Get rows wqhich are not masked
        dels = np.logical_and(not_masked, dels)  # And make dels just be unmasked rows with NaNs

        ret.del_rows(np.logical_not(dels))  # Del the those rows
        return ret

    def del_rows(self, col=None, val=None, invert=False):
        """Search in the numerica data for the lines that match and deletes the corresponding rows.

        Args:
            col (list,slice,int,string, re, callable or None):
                Column containg values to search for.
            val (float or callable):
                Specifies rows to delete. Maybe:
                    -   None - in which case the *col* argument is used to identify rows to be deleted,
                    -   a float in which case rows whose columncol = val are deleted
                    -   or a function - in which case rows where the function evaluates to be true are deleted.
                    -   a tuple, in which case rows where column col takes value between the minium and maximum of
                        the tuple are deleted.

        Keyword Arguments:
            invert (bool):
                Specifies whether to invert the logic of the test to delete a row. If True, keep the rows
                that would have been deleted otherwise.

        Returns:
            self:
                The current :py:class:`DataFile` object

        Note:
            If col is None, then all rows with masked data are deleted

            if *col* is callable then it is passed each row as a :py:class:`DataArray` and if it returns
            True, then the row will be deleted or kept depending on the value of *invert*.

            If *val* is a callable it should take two arguments - a float and a
            list. The float is the value of the current row that corresponds to column col abd the second
            argument is the current row.

        Todo:
            Implement val is a tuple for deletinging in a range of values.
        """
        if col is None:
            self.data = ma.mask_rows(self.data)
            self.data = ma.compress_rows(self.data)
        else:
            if isinstance(col, slice) and val is None:  # delete rows with a slice to make a list of indices
                indices = col.indices(len(self))
                col = list(range(*indices))
            elif callable(col) and val is None:  # Delete rows usinga callalble taking the whole row
                col = [r.i for r in self.rows() if col(r)]
            elif isIterable(col) and all_type(col, bool):  # Delete rows by a list of booleans
                if len(col) < len(self):
                    col.extend([False] * (len(self) - len(col)))
                self.data = self.data[col]
                return self
            if isIterable(col) and all_type(col, int_types) and val is None and not invert:
                col.sort(reverse=True)
                for c in col:
                    self.del_rows(c)
            elif isinstance(col, list) and all_type(col, int_types) and val is None and invert:
                for i in range(len(self) - 1, -1, -1):
                    if i not in col:
                        self.del_rows(i)
            elif isinstance(col, int_types) and val is None and not invert:
                tmp_mask = self.mask
                tmp_setas = self.data._setas.clone
                self.data = np.delete(self.data, col, 0)
                self.data.mask = np.delete(tmp_mask, col, 0)
                self.data._setas = tmp_setas
            elif isinstance(col, int_types) and val is None and invert:
                self.del_rows([c], invert=invert)
            else:
                col = self.find_col(col)
                d = self.column(col)
                if callable(val):
                    rows = np.nonzero(
                        [(bool(val(x[col], x) and bool(x[col] is not ma.masked)) != invert) for x in self]
                    )[0]
                elif isinstance(val, float):
                    rows = np.nonzero([bool(x == val) != invert for x in d])[0]
                elif isIterable(val) and len(val) == 2:
                    (upper, lower) = (max(list(val)), min(list(val)))
                    rows = np.nonzero([bool(lower <= x <= upper) != invert for x in d])[0]
                else:
                    raise SyntaxError(
                        "If val is specified it must be a float,callable, or iterable object of length 2"
                    )
                tmp_mask = self.mask
                tmp_setas = self.data._setas.clone
                self.data = np.delete(self.data, rows, 0)
                self.data.mask = np.delete(tmp_mask, rows, 0)
                self.data._setas = tmp_setas
        return self

    def dir(self, pattern=None):
        """Return a list of keys in the metadata, filtering wiht a regular expression if necessary.

        Keyword Arguments:
            pattern (string or re):
                is a regular expression or None to list all keys

        Returns:
            list:
                A list of metadata keys.
        """
        if pattern is None:
            return list(self.metadata.keys())
        if isinstance(pattern, _pattern_type):
            test = pattern
        else:
            test = re.compile(pattern)
        possible = [x for x in self.metadata.keys() if test.search(x)]
        return possible

    def filter(self, func=None, cols=None, reset=True):
        """Set the mask on rows of data by evaluating a function for each row.

        Args:
            func (callable):
                is a callable object that should take a single list as a p[arameter representing one row.
            cols (list):
                a list of column indices that are used to form the list of values passed to func.
            reset (bool):
                determines whether the mask is reset before doing the filter (otherwise rows already
                masked out will be ignored in the filter (so the filter is logically or'd)) The default value of
                None results in a complete row being passed into func.

        Returns:
            self: The current :py:class:`DataFile` object with the mask set
        """
        if cols is not None:
            cols = [self.find_col(c) for c in cols]
        if reset:
            self.data.mask = False
        for r in self.rows():
            if cols is None:
                self.mask[r.i, :] = not func(r)
            else:
                self.mask[r.i, :] = not func(r[cols])
        return self

    def get_filename(self, mode):
        """Force the user to choose a new filename using a system dialog box.

        Args:
            mode (string):
                The mode of file operation to be used when calling the dialog box

        Returns:
            str:
                The new filename

        Note:
            The filename attribute of the current instance is updated by this method as well.
        """
        self.filename = self.__file_dialog(mode)
        return self.filename

    def insert_rows(self, row, new_data):
        """Insert new_data into the data array at position row. This is a wrapper for numpy.insert.

        Args:
            row (int):
                Data row to insert into
            new_data (numpy array):
                An array with an equal number of columns as the main data array containing the new row(s) of
                data to insert

        Returns:
            self:
                A copy of the modified :py:class:`DataFile` object
        """
        self.data = np.insert(self.data, row, new_data, 0)
        return self

    def load(self, *args, **kargs):
        """Load the :py:class:`DataFile` in from disc guessing a better subclass if necessary.

        Args:
            filename (string or None):
                path to file to load

        Keyword Arguments:
            auto_load (bool):
                If True (default) then the load routine tries all the subclasses of :py:class:`DataFile` in turn to
                load the file
            filetype (:py:class:`DataFile`, str):
                If not none then tries using filetype as the loader.

        Returns:
            (DataFile):
                A copy of the loaded :py:data:`DataFile` instance

        Note:
            If *filetupe* is a string, then it is first tried as an exact match to a subclass name, otherwise it
            is used as a partial match and the first class in priority order is that matches is used.

            Some subclasses can be found in the :py:mod:`Stoner.FileFormats` module.

            Each subclass is scanned in turn for a class attribute priority which governs the order in which they
            are tried. Subclasses which can make an early positive determination that a file has the correct format
            can have higher priority levels. Classes should return a suitable expcetion if they fail to load the file.

            If not class can load a file successfully then a RunttimeError exception is raised.
        """
        filename = args[0] if len(args) > 0 else None
        filename = kargs.pop("filename", filename)
        filetype = kargs.pop("filetype", None)
        auto_load = kargs.pop("auto_load", filetype is None)

        if isinstance(filetype, string_types):  # We can specify filetype as part of name
            try:
                filetype = subclasses(DataFile)[filetype]  # pylint: disable=E1136
            except KeyError:
                for k, cls in subclasses(DataFile).items():  # pylint: disable=E1136, E1101
                    if filetype in k:
                        filetype = cls
                        break
                else:
                    raise ValueError(f"Unrecognised class name for file type: {filetype}")

        if filename is None or (isinstance(filename, bool) and not filename):
            filename = self.__file_dialog("r")
        elif isinstance(filename, io.IOBase):  # Opened file
            self.filename = filename.name
        else:
            self.filename = filename
        if not path.exists(self.filename):
            raise IOError(f"Cannot find {self.filename} to load")
        if filemagic is not None:
            with filemagic(flags=MAGIC_MIME_TYPE) as m:
                mimetype = m.id_filename(filename)
            if self.debug:
                print(f"Mimetype:{mimetype}")
        cls = self.__class__
        failed = True
        if auto_load:  # We're going to try every subclass we canA
            for cls in subclasses(DataFile).values():  # pylint: disable=E1136, E1101
                if self.debug:
                    print(cls.__name__)
                try:
                    if (
                        filemagic is not None and mimetype not in cls.mime_type
                    ):  # short circuit for non-=matching mime-types
                        if self.debug:
                            print(f"Skipping {cls.__name__} due to mismatcb mime type {cls.mime_type}")
                        continue
                    test = cls()
                    if self.debug and filemagic is not None:
                        print(f"Trying: {cls.__name__} =mimetype {test.mime_type}")

                    test = test._load(self.filename, auto_load=False, *args, **kargs)
                    if test is None:
                        raise SyntaxError(f"Class {cls.__name__}'s _load returned None !!")
                    try:
                        kargs = test._kargs
                        delattr(test, "_kargs")
                    except AttributeError:
                        pass

                    if self.debug:
                        print("Passed Load")
                    failed = False
                    test["Loaded as"] = cls.__name__
                    if self.debug:
                        print(f"Test matadata: {test.metadata}")
                    copy_into(test, self)
                    if self.debug:
                        print(f"Self matadata: {self.metadata}")

                    break
                except StonerLoadError as e:
                    if self.debug:
                        print(f"Failed Load: {e}")
                    continue
                except UnicodeDecodeError:
                    print(f"{cls, filename} Failed with a uncicode decode error for { format_exc()}\n")
                    continue
            else:
                raise StonerUnrecognisedFormat(
                    f"Ran out of subclasses to try and load {filename} as."
                    + f" Recognised filetype are:{list(subclasses(DataFile).keys())}"  # pylint: disable=E1101
                )
        else:
            if filetype is None:
                test = cls()
                test = test._load(self.filename, *args, **kargs)
                kargs = getattr(test, "_kargs", kargs)
                self["Loaded as"] = cls.__name__
                self.data = test.data
                self.metadata.update(test.metadata)
                failed = False
            elif issubclass(filetype, DataFile):
                test = filetype()
                test = test._load(self.filename, *args, **kargs)
                kargs = getattr(test, "_kargs", kargs)
                self["Loaded as"] = filetype.__name__
                self.data = test.data
                self.metadata.update(test.metadata)
                self.column_headers = test.column_headers
                failed = False
            elif isinstance(filetype, DataFile):
                test = filetype.clone
                test = test._load(self.filename, *args, **kargs)
                kargs = getattr(test, "_kargs", kargs)
                self["Loaded as"] = filetype.__name__
                self.data = test.data
                self.metadata.update(test.metadata)
                self.column_headers = test.column_headers
                failed = False

        if failed:
            raise SyntaxError("Failed to load file")
        for k, i in kargs.items():
            if not callable(getattr(self, k, lambda x: False)):
                setattr(self, k, i)
        self._kargs = kargs
        return self

    def rename(self, old_col, new_col):
        """Rename columns without changing the underlying data.

        Args:
            old_col (string, int, re):
                Old column index or name (using standard rules)
            new_col (string):
                New name of column

        Returns:
            self:
                A copy of the modified :py:class:`DataFile` instance
        """
        old_col = self.find_col(old_col)
        self.column_headers[old_col] = new_col
        return self

    def reorder_columns(self, cols, headers_too=True, setas_too=True):
        """Construct a new data array from the original data by assembling the columns in the order given.

        Args:
            cols (list of column indices):
                (referred to the oriignal data set) from which to assemble the new data set
            headers_too (bool):
                Reorder the column headers in the same way as the data (defaults to True)
            setas_too (bool):
                Reorder the column assignments in the same way as the data (defaults to True)

        Returns:
            self:
                A copy of the modified :py:class:`DataFile` object
        """
        if headers_too:
            column_headers = [self.column_headers[self.find_col(x)] for x in cols]
        else:
            column_headers = self.column_headers
        if setas_too:
            setas = [self.setas[self.find_col(x)] for x in cols]
        else:
            setas = self.setas.clone

        newdata = np.atleast_2d(self.data[:, self.find_col(cols.pop(0))])
        for col in cols:
            newdata = np.append(newdata, np.atleast_2d(self.data[:, self.find_col(col)]), axis=0)
        self.data = DataArray(np.transpose(newdata))
        self.setas = setas
        self.column_headers = column_headers
        return self

    def rows(self, not_masked=False, reset=False):
        """Iterate over rows of data.

        Keyword Arguments:
            not_masked(bool):
                If a row is masked and this is true, then don't return this row.
            reset (bool):
                If true then reset the iterator (immediately stops the current iteration without returning any data)./

        Yields:
            1D array: Returns the next row of data
        """
        for ix, row in enumerate(self.data):
            if not isinstance(row, DataArray):
                row = DataArray([row])
                row.i = ix
                row.setas = self.setas
            if ma.is_masked(row) and not_masked:
                continue
            if reset:
                return
            else:
                yield row

    def save(self, filename=None, **kargs):
        """Save a string representation of the current DataFile object into the file 'filename'.

        Args:
            filename (string, bool or None):
                Filename to save data as, if this is None then the current filename for the object is used. If this
                is not set, then then a file dialog is used. If filename is False then a file dialog is forced.
            as_loaded (bool,str):
                If True, then the *Loaded as* key is inspected to see what the original class of the DataFile was
                and then this class' save method is used to save the data. If a str then
                the keyword value is interpreted as the name of a subclass of the the current DataFile.

        Returns:
            self:
                The current :py:class:`DataFile` object
        """
        as_loaded = kargs.pop("as_loaded", False)
        if filename is None:
            filename = self.filename
        if filename is None or (isinstance(filename, bool) and not filename):
            # now go and ask for one
            filename = self.__file_dialog("w")
        if as_loaded:
            if (
                isinstance(as_loaded, bool) and "Loaded as" in self
            ):  # Use the Loaded as key to find a different save routine
                cls = subclasses(DataFile)[self["Loaded as"]]  # pylint: disable=unsubscriptable-object
            elif isinstance(as_loaded, string_types) and as_loaded in subclasses(
                DataFile
            ):  # pylint: disable=E1136, E1135
                cls = subclasses(DataFile)[as_loaded]  # pylint: disable=unsubscriptable-object
            else:
                raise ValueError(
                    f"{as_loaded} cannot be interpreted as a valid sub class of {type(self)}"
                    + f" so cannot be used to save this data"
                )
            ret = cls(self).save(filename)
            self.filename = ret.filename
            return self
        # Normalise the extension to ensure it's something we like...
        filename, ext = os.path.splitext(filename)
        if f"*.{ext}" not in DataFile.patterns:  # pylint: disable=unsupported-membership-test
            ext = DataFile.patterns[0][2:]  # pylint: disable=unsubscriptable-object
        filename = f"{filename}.{ext}"
        header = ["TDI Format 1.5"]
        header.extend(self.column_headers[: self.data.shape[1]])
        header = "\t".join(header)
        mdkeys = sorted(self.metadata)
        if len(mdkeys) > len(self):
            mdremains = mdkeys[len(self) :]
            mdkeys = mdkeys[0 : len(self)]
        else:
            mdremains = []
        mdtext = np.array([self.metadata.export(k) for k in mdkeys])
        if len(mdtext) < len(self):
            mdtext = np.append(mdtext, np.zeros(len(self) - len(mdtext), dtype=str))
        data_out = np.column_stack([mdtext, self.data])
        fmt = ["%s"] * data_out.shape[1]
        with io.open(filename, "wb") as f:
            np.savetxt(f, data_out, fmt=fmt, header=header, delimiter="\t", comments="")
            for k in mdremains:
                f.write(str2bytes(self.metadata.export(k) + "\n"))  # (str2bytes(self.metadata.export(k) + "\n"))

        self.filename = filename
        return self

    def swap_column(self, *swp, **kargs):
        """Swap pairs of columns in the data.

        Useful for reordering data for idiot programs that expect columns in a fixed order.

        Args:
            swp  (tuple of list of tuples of two elements):
                Each element will be iused as a column index (using the normal rules
                for matching columns).  The two elements represent the two
                columns that are to be swapped.
            headers_too (bool):
                Indicates the column headers are swapped as well

        Returns:
            self:
                A copy of the modified :py:class:`DataFile` objects

        Note:
            If swp is a list, then the function is called recursively on each
            element of the list. Thus in principle the @swp could contain
            lists of lists of tuples
        """
        self.data.swap_column(*swp, **kargs)
        return self

    def to_pandas(self):
        """Create a pandas DataFrame from a :py:class:`Stoner.Data` object.

        Notes:
            In addition to transferring the numerical data, the DataFrame's columns are set to
            a multi-level index of the :py:attr:`Stoner.Data.column_headers` and :py:attr:`Stoner.Data.setas`
            calues. A pandas DataFrame extension attribute, *metadata* is registered and is used to store
            the metada from the :py:class:1Stoner.Data` object. This pandas extension attribute is in fact a trivial
            subclass of the :py:class:`Stoner.core.typeHintedDict`.

            The inverse operation can be carried out simply by passing a DataFrame into the copnstructor of the
            :py:class:`Stoner.Data` object.

        Raises:
            **NotImplementedError** if pandas didn't import correctly.
        """
        if pd is None:
            raise NotImplementedError("Pandas not available")
        idx = pd.MultiIndex.from_tuples(zip(*[self.column_headers, self.setas]), names=("Headers", "Setas"))
        df = pd.DataFrame(self.data, columns=idx)
        df.metadata.update(self.metadata)
        return df
