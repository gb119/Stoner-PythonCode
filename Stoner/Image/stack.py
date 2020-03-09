# -*- coding: utf-8 -*-
"""Provide variants of :class:`Stoner.Image.ImageFolder` that store images efficiently in 3D numpy arrays."""
__all__ = ["ImageStackMixin", "ImageStack", "ImageStack"]
import warnings

import numpy as np

from Stoner.compat import string_types, int_types
from .core import ImageArray, ImageFile
from .folders import ImageFolder, ImageFolderMixin
from Stoner.Core import regexpDict, typeHintedDict
from Stoner.Folders import DiskBasedFolderMixin, baseFolder

IM_SIZE = (512, 672)  # Standard Kerr image size
AN_IM_SIZE = (554, 672)  # Kerr image with annotation not cropped


def _load_ImageArray(f, **kargs):
    """Utility method to create and image array."""
    kargs.pop("Img_num", None)  # REemove img_num if it exists
    return ImageArray(f, **kargs)


class ImageStackMixin:

    """Implement an interface for a baseFolder to store images in a 3D numpy array for faster access."""

    _defaults = {"type": ImageFile}

    def __init__(self, *args, **kargs):
        """Initialise an ImageStack's pricate data and provide a type argument."""
        self._stack = np.atleast_3d(np.ma.MaskedArray([]))
        self._metadata = regexpDict()
        self._names = list()
        self._sizes = np.array([], dtype=int).reshape(0, 2)

        if not len(args):
            super(ImageStackMixin, self).__init__(**kargs)
            return None  # No further initialisation
        other = args[0]
        if isinstance(other, ImageStackMixin):
            super(ImageStackMixin, self).__init__(*args[1:], **kargs)
            self._stack = other._stack
            self._metadata = other._metadata
            self._names = other._names
            self._sizes = other._sizes
        elif isinstance(other, ImageFolder):  # ImageFolder can already init from itself
            super(ImageStackMixin, self).__init__(*args, **kargs)
        elif (
            isinstance(other, np.ndarray) and len(other.shape) == 3
        ):  # Initialise with 3D numpy array, first coordinate is number of images
            super(ImageStackMixin, self).__init__(*args[1:], **kargs)
            self.imarray = other
            self._sizes = np.ones((other.shape[0], 2), dtype=int) * other.shape[1:]
            self._names = ["Untitled-{}".format(d) for d in range(other.shape[0])]
            for n in self._names:
                self._metadata[n] = typeHintedDict()
        elif isinstance(other, list):
            try:
                other = [ImageFile(i) for i in other]
            except Exception:
                raise ValueError("Failed to initialise ImageStack with list input")
            super(ImageStackMixin, self).__init__(*args[1:], **kargs)
            for ot in other:
                self.append(ot)
            del self[-1]  # Bit of a hack to get rid of initialised zeros data -
            # this poss needs changing in the append method
        else:
            super(ImageStackMixin, self).__init__(*args, **kargs)

    def __lookup__(self, name):
        """Stub for other classes to implement.

        Parameters:
            name(str): Name of an object

        Returns:
            A key in whatever form the :py:meth:`baseFolder.__getter__` will accept.

        Note:
            We're in the base class here, so we don't call super() if we can't handle this, then we're stuffed!
        """
        if isinstance(name, int_types):
            try:
                self._stack[:, :, name]
            except IndexError:
                raise KeyError("{} is out of range for accessing the ImageStack.".format(name))
            return name
        elif name not in self.__names__():
            name = self._metadata.__lookup__(name)
        return list(self._metadata.keys()).index(name)  # return the matching index of the name

    def __names__(self):
        """Stub method to return a list of names of all objects that can be indexed for __getter__.

        Note:
            We're in the base class here, so we don't call super() if we can't handle this, then we're stuffed!
        """
        return list(self._metadata.keys())

    def __getter__(self, name, instantiate=True):
        """Stub method to do whatever is needed to transform a key to a metadataObject.

        Parameters:
            name (key type): The canonical mapping key to get the dataObject. By default
                the baseFolder class uses a :py:class:`regexpDict` to store objects in.

        Keyword Arguments:
            instatiate (bool): If True (default) then always return a metadataObject. If False,
                the __getter__ method may return a key that can be used by it later to actually get the
                metadataObject. If None, then will return whatever is helf in the object cache, either instance or name.

        Returns:
            (metadataObject): The metadataObject

            Note:
            We're in the base class here, so we don't call super() if we can't handle this, then we're stuffed!


        """
        try:
            idx = self.__lookup__(name)
        except KeyError:  # If we don't seem to have the name then see if we can fall back to something else like a DiskBasedFolderMixin
            return super(ImageStackMixin, self).__getter__(name, instantiate)
        if isinstance(instantiate, bool) and not instantiate:
            return self.__names__()[idx]
        else:
            instance = self._instantiate(idx)
            return self._update_from_object_attrs(instance)

    def __setter__(self, name, value, force_insert=False):
        """Stub to setting routine to store a metadataObject.

        Parameters:
            name (string):
                the named object to write - may be an existing or new name
            value (metadataObject):
                the value to store.

        Note:
            We're in the base class here, so we don't call super() if we can't handle this, then we're stuffed!
        """
        if isinstance(name, int_types):
            try:
                name = self.__names__()[name]
            except IndexError:
                name = self.make_name(value)
        if name is None:
            name = self.make_name(value)
        try:
            if force_insert:
                raise KeyError("Fake force insert")
            idx = self.__lookup__(name)
        except KeyError:  # Ok we're appending here
            if isinstance(value, string_types):  # Append with a filename, call __getter__
                value = self.__getter__(value, instantiate=True)  # self.__getter__ will also insert if necessary
                return None
            else:  # Append with real value
                idx = len(self)
                return self.__inserter__(idx, name, value)
        else:
            value = self.type(value)  # ensure type if a bare numpy array was given
            self._sizes[idx] = value.shape
        self._metadata[name] = value.metadata
        if hasattr(value, "image"):
            value = value.image
        row, col = value.shape
        pag = len(self._sizes)
        new_size = self.max_size + (pag,)
        if new_size[2] == 1:
            dtype = value.dtype
        else:
            dtype = None
        self._resize_stack(new_size, dtype=dtype)
        self._stack[:row, :col, idx] = value

    def __inserter__(self, ix, name, value):
        """Provide an efficient insert into the stack.

        The default implementation is rather slow about inserting since it has to clear the data folder and then rebuild it entry by entry. This does
        a simple insert."""
        value = ImageFile(value)  # ensure we have some metadata
        self._names.insert(ix, name)
        self._metadata[name] = value.metadata
        self._sizes = np.insert(self._sizes, ix, value.shape, axis=0)
        new_size = self.max_size + (len(self._names),)
        if new_size[2] == 1:
            dtype = value.dtype
        else:
            dtype = None
        self._resize_stack(new_size, dtype=dtype)
        self._stack = np.insert(self._stack, ix, np.zeros(self.max_size), axis=2)
        row, col = value.shape
        self._stack[:row, :col, ix] = value.data

    def __deleter__(self, ix):
        """Deletes an object from the baseFolder.

        Parameters:
            ix(str): Index to delete, should be within +- the lengthe length of the folder.

        Note:
            We're in the base class here, so we don't call super() if we can't handle this, then we're stuffed!

        """
        idx = self.__lookup__(ix)
        name = list(self.__names__())[idx]
        del self._metadata[name]
        self._stack = np.delete(self._stack, idx, axis=2)
        del self._names[idx]
        self._sizes = np.delete(self._sizes, ix, axis=0)

    def __clear__(self):
        """"Clears all stored :py:class:`Stoner.Core.metadataObject` instances stored.

        Note:
            We're in the base class here, so we don't call super() if we can't handle this, then we're stuffed!

        """
        self._metadata = regexpDict()
        self._stack = np.atleast_3d(np.ma.MaskedArray([]))

    ###########################################################################
    ###################      Special methods     ##############################

    def __floordiv__(self, other):
        """Calculate and XMCD ratio on the images."""
        if not isinstance(other, ImageStackMixin):
            return NotImplemented
        if self._stack.dtype != other._stack.dtype:
            raise ValueError(
                f"Only ImageFiles with the same type of underlying image data can be used to calculate an XMCD ratio."
                + "Mimatch is {self._stack.dtype} vs {other._stack.dtype}"
            )
        if self._stack.dtype.kind != "f":
            ret = self.clone.convert(float)
            other = other.clone.convert(float)
        else:
            ret = self.clone
        ret._stack = (ret._stack - other._stack) / (ret._stack + other._stack)

        return ret

    ###########################################################################
    ###################      Private methods     ##############################

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
        tmp.metadata = self._metadata[self.__names__()[idx]]
        tmp._fromstack = True
        return tmp

    def _resize_stack(self, new_size, dtype=None):
        """Create a new stack with a new size."""
        old_size = self._stack.shape
        if old_size == new_size:
            return new_size
        if dtype is None:
            dtype = self._stack.dtype
        row, col, pag = tuple([min(o, n) for o, n in zip(old_size, new_size)])

        new = np.ma.zeros(new_size, dtype=dtype)
        new[:row, :col, :pag] = self._stack[:row, :col, :pag]
        self._stack = new
        return row, col, pag

    ###########################################################################
    ################### Properties of ImageStack ##############################

    @property
    def imarray(self):
        """"Produce the 3D stack of images - as [image,x,y]"""
        return np.transpose(self._stack, (2, 0, 1))

    @imarray.setter
    def imarray(self, value):
        """"Set the 3D stack of images - as [image,x,y]"""
        value = np.ma.MaskedArray(np.atleast_3d(value))
        self._stack = np.transpose(value, (1, 2, 0))

    @property
    def max_size(self):
        """Get the biggest image dimensions in the stack."""
        if np.prod(self._sizes.shape) == 0:
            return (0, 0)
        return (self._sizes[:, 0].max(), self._sizes[:, 1].max())

    @property
    def shape(self):
        """Return the stack shape - after re-ordering the indices."""
        x, y, z = self._stack.shape
        return (z, x, y)

    ###########################################################################
    ###################         Public  methods         #######################

    def convert(self, dtype, force_copy=False, uniform=False, normalise=True):
        """
        Convert an image to the requested data-type.
        
        Warnings are issued in case of precision loss, or when negative values
        are clipped during conversion to unsigned integer types (sign loss).
        
        Floating point values are expected to be normalized and will be clipped
        to the range [0.0, 1.0] or [-1.0, 1.0] when converting to unsigned or
        signed integers respectively.
        
        Numbers are not shifted to the negative side when converting from
        unsigned to signed integer types. Negative values will be clipped when
        converting to unsigned integers.
        
        Parameters
        ----------
        image : ndarray
        Input image.
        dtype : dtype
        Target data-type.
        force_copy : bool
        Force a copy of the data, irrespective of its current dtype.
        uniform : bool
        Uniformly quantize the floating point range to the integer range.
        By default (uniform=False) floating point values are scaled and
        rounded to the nearest integers, which minimizes back and forth
        conversion errors.
        normalise : bool
        When converting from int types to float normalise the resulting array
        by the maximum allowed value of the int type.
        
        References
        ----------
        (1) DirectX data conversion rules.
        http://msdn.microsoft.com/en-us/library/windows/desktop/dd607323%28v=vs.85%29.aspx
        (2) Data Conversions.
        In "OpenGL ES 2.0 Specification v2.0.25", pp 7-8. Khronos Group, 2010.
        (3) Proper treatment of pixels as integers. A.W. Paeth.
        In "Graphics Gems I", pp 249-256. Morgan Kaufmann, 1990.
        (4) Dirty Pixels. J. Blinn.
        In "Jim Blinn's corner: Dirty Pixels", pp 47-57. Morgan Kaufmann, 1998.
        
        """
        from .imagefuncs import convert

        # Aactually this is just a pass through for the imagefuncs.convert routine
        self._stack = convert(self._stack, dtype, force_copy=force_copy, uniform=uniform, normalise=normalise)
        return self

    def asfloat(self, normalise=True, clip=False, clip_negative=False, **kargs):
        """Convert stack to floating point type.
        Analagous behaviour to ImageFile.asfloat()

        If currently an int type and normalise then floats will be normalised
        to the maximum allowed value of the int type.
        If currently a float type then no change occurs.
        If clip_negative then clip values outside the range 0,1

        Keyword Arguments:
            normalise(bool):
                normalise the image to the max value of current int type
            clip(bool):
                clip resulting range to values between -1 and 1
            clip_negative(bool):
                clip range further to 0,1
        """
        if self.imarray.dtype.kind == "f":
            pass
        else:
            self.convert(dtype=np.float64, normalise=normalise)
        if "clip_neg" in kargs:
            warnings.warn(
                "clip_neg argument renamed to clip_negative in ImageStack. This will cause an error in future versions of the Stoner Package."
            )
            clip_negative = kargs.pop("clip_neg")
        if clip or clip_negative:
            self.each.clip_intensity(clip_negative=clip_negative)
        return self

    def dtype_limits(self, clip_negative=True):
        """Return intensity limits, i.e. (min, max) tuple, of imarray dtype.

        Keyword Arguments:
            clip_negative(bool):
                If True, clip the negative range (i.e. return 0 for min intensity)
                even if the image dtype allows negative values.
        Returns:
            (imin,imax) (tuple):
                Lower and upper intensity limits.
        """
        return self[0].dtype_limits

    ###########################################################################
    ################### Depricated Compaibility methods #######################

    def correct_drifts(self, refindex, threshold=0.005, upsample_factor=50, box=None):
        """Align images to correct for image drift.

        Pass through to ImageArray.corret_drift.

        Arg:
            refindex: int or str
                index or name of the reference image to use for zero drift
        Keyword Arguments:
            threshold(float): see ImageArray.correct_drift
            upsample_factor(int): see ImageArray.correct_drift
            box: see ImageArray.correct_drift

        """
        warnings.warn("correct_drift is a depricated method for an image stack - consider using align.")
        ref = self[refindex]
        self.apply_all("correct_drift", ref, threshold=threshold, upsample_factor=upsample_factor, box=box)

    def crop_stack(self, box):
        """Crop the imagestack.
        Crops to the box given

        Args:
            box(array or list of type int):
                [xmin,xmax,ymin,ymax]

        Returns:
            (ImageStack):
                cropped images
        """
        warnings.warn("crop_stack is depricated - sam effect can be achieved with crop(box)")
        self.each.crop(box)

    def show(self):
        """Pass through to :py:meth:`Stoner.Image.ImageFolder.view`"""
        warnings.warn("show() is depricated in favour of ImageFolder.view()")
        return self.view()


class StackAnalysisMixin:
    """Add some analysis capability to ImageStack. These functions may override
       ImageFile functions but do them efficiently for a numpy stack of
       images.
       """

    def subtract(self, background, contrast=16, clip_intensity=True):
        """Subtract a background image (or index) from all images in the stack.

        The formula used is new = (ImageArray - background) * contrast + 0.5
        If clip_intensity then clip negative intensities to 0. Array is always
        converted to float for this method.

        Arg:
            background(int or np.ndarray or ImageFile):
                the background image to subtract. If int is given this is used
                as an index on the stack.
        Keyword Arguments:
            contrast(float):
                Determines contrast of resulting image
            clip_intensity(bool):
                whether to clip the image intensities in range (0,1) after subtraction
        """
        self.asfloat(normalise=True, clip_negative=False)
        if isinstance(background, int):
            bg = self[background]
        if isinstance(bg.ImageFile):
            bg = bg.image
        bg = bg.view(ImageArray).asfloat(normalise=True, clip_negative=False)
        bg = np.tile(bg, (1, 1, len(self)))
        self._stack = contrast * (self._stack - bg) + 0.5
        if clip_intensity:
            self.clip_intensity()


class ImageStack(StackAnalysisMixin, ImageStackMixin, ImageFolderMixin, DiskBasedFolderMixin, baseFolder):

    """An alternative implementation of an image stack based on baseFolder."""

    pass
