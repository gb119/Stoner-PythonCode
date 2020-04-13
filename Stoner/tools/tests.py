# -*- coding: utf-8 -*-
"""Utility functions that test whether things are true or not."""

__all__ = [
    "all_size",
    "all_type",
    "isAnyNone",
    "isComparable",
    "isIterable",
    "isLikeList",
    "isNone",
    "isProperty",
    "isTuple",
]

from collections.abc import Iterable
from numpy import ndarray, dtype, isnan, logical_and  # pylint: disable=redefined-builtin

from ..compat import string_types


def all_size(iterator, size=None):
    """Check whether each element of *iterator* is the same length/shape.

    Arguments:
        iterator (Iterable): list or other iterable of things with a length or shape

    Keyword Arguments:
        size(int, tuple or None): Required size of each item in iterator.

    Returns:
        True if all objects are the size specified (or the same size if size is None).
    """
    if hasattr(iterator[0], "shape"):
        sizer = lambda x: x.shape
    else:
        sizer = len

    if size is None:
        size = sizer(iterator[0])
    ret = False
    for i in iterator:
        if sizer(i) != size:
            break
    else:
        ret = True
    return ret


def all_type(iterator, typ):
    """Determines if an interable omnly contains a common type.

    Arguments:
        iterator (Iterable):
            The object to check if it is all iterable
        typ (class):
            The type to check for.

    Returns:
        True if all elements are of the type typ, or False if not.

    Notes:
        Routine will iterate the *iterator* and break when an element is not of
        the search type *typ*.
    """
    ret = False
    if isinstance(iterator, ndarray):  # Try to short circuit for arrays
        try:
            return iterator.dtype == dtype(typ)
        except TypeError:
            pass
    if isIterable(iterator):
        for i in iterator:
            if not isinstance(i, typ):
                break
        else:
            ret = True
    return ret


def isAnyNone(*args):
    """Intelligently check whether any of the inputs are None."""
    for arg in args:
        if arg is None:
            return True
    return False


def isComparable(v1, v2):
    """Returns true if v1 and v2 can be compared sensibly

    Args:
        v1,v2 (any):
            Two values to compare

    Returns:
        False if both v1 and v2 are numerical and both nan, otherwise True.
    """
    try:
        return not (isnan(v1) and isnan(v2))
    except TypeError:
        return True
    except ValueError:
        try:
            return not logical_and(isnan(v1), isnan(v2)).any()
        except TypeError:
            return False


def isIterable(value):
    """Chack to see if a value is iterable.

    Args:
        value :
            Entitiy to check if it is iterable

    Returns:
        (bool):
            True if value is an instance of collections.Iterable.
    """
    return isinstance(value, Iterable)


def isLikeList(value):
    """Returns True if value is an iterable but not a string."""
    return isIterable(value) and not isinstance(value, string_types)


def isNone(iterator):
    """Returns True if input is None or an empty iterator, or an iterator of None.

    Args:
        iterator (None or Iterable):

    Returns:
        True if iterator is None, empty or full of None.
    """
    if iterator is None:
        ret = True
    elif isIterable(iterator) and not isinstance(iterator, string_types):
        try:
            l = len(iterator)
        except TypeError:
            l = 0
        if l == 0:  # pylint: disable=len-as-condition
            ret = True
        else:
            for i in iterator:
                if i is not None:
                    ret = False
                    break
            else:
                ret = True
    else:
        ret = False
    return ret


def isProperty(obj, name):
    """Check whether an attribute of an object or class is a property.

    Args:
        obj (instance or class):
            Thing that has the attribute to check
        name (str):
            Name of the attrbiute that might be a property

    Returns:
        (bool):
            Whether the name is a property or not.
    """
    if not isinstance(obj, type):
        obj = obj.__class__
    elif not issubclass(obj, object):
        raise TypeError(
            "Can only check for property status on attributes of an object or a class not a {}".format(type(obj))
        )
    return hasattr(obj, name) and isinstance(getattr(obj, name), property)


def isTuple(obj, *args, **kargs):
    """Determine if obj is a tuple of a certain signature.

    Args:
        obj(object):
            The object to check
        *args(type):
            Each of the suceeding arguments are used to determine the expected type of each element.

    Keywoprd Arguments:
        strict(bool):
            Whether the elements of the tuple have to be exactly the type specified or just castable as the type

    Returns:
        (bool):
            True if obj is a matching tuple.
    """
    strict = kargs.pop("strict", True)
    if not isinstance(obj, tuple):
        return False
    if args and len(obj) != len(args):
        return False
    for t, e in zip(args, obj):
        if strict:
            if not isinstance(e, t):
                bad = True
                break
        else:
            try:
                _ = t(e)
            except ValueError:
                bad = True
                break
    else:
        bad = False
    return not bad
