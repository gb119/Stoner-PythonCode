#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
The main Data Class definition
"""

from sys import float_info
from Stoner.Analysis import AnalysisMixin
from Stoner.analysis.fitting.mixins import FittingMixin
from Stoner.analysis.columns import ColumnOps
from Stoner.analysis.filtering import FilteringOps
from Stoner.plot import PlotMixin
from Stoner.Core import DataFile

from Stoner.tools import format_error


class Data(AnalysisMixin, FittingMixin, ColumnOps, FilteringOps, PlotMixin, DataFile):

    """A merged class of :py:class:`Stoner.Core.DataFile`, :py:class:`Stoner.Analysis.AnalysisMixin` and
    :py:class:`Stoner.plot.PlotMixin`

    Also has the :py:mod:`Stoner.FielFormats` loaded redy for use.
    This 'kitchen-sink' class is intended as a convenience for writing scripts that carry out both plotting and
    analysis on data files.
    """

    def format(self, key, **kargs):
        r"""Return the contents of key pretty formatted using :py:func:`format_error`.

        Args:
            fmt (str): Specify the output format, opyions are:

                *  "text" - plain text output
                * "latex" - latex output
                * "html" - html entities

            escape (bool):
                Specifies whether to escape the prefix and units for unprintable characters in non
                text formats )default False)
            mode (string):
                If "float" (default) the number is formatted as is, if "eng" the value and error is converted
                to the next samllest power of 1000 and the appropriate SI index appended. If mode is "sci" then a
                scientifc, i.e. mantissa and exponent format is used.
            units (string):
                A suffix providing the units of the value. If si mode is used, then appropriate si
                prefixes are prepended to the units string. In LaTeX mode, the units string is embedded in \mathrm
            prefix (string):
                A prefix string that should be included before the value and error string. in LaTeX mode this is
                inside the math-mode markers, but not embedded in \mathrm.

        Returns:
            A pretty string representation.

        The if key="key", then the value is self["key"], the error is self["key err"], the default prefix is
        self["key label"]+"=" or "key=", the units are self["key units"] or "".

        """
        mode = kargs.pop("mode", "float")
        units = kargs.pop("units", self.get(key + " units", ""))
        prefix = kargs.pop("prefix", "{} = ".format(self.get(key + " label", "{}".format(key))))
        latex = kargs.pop("latex", False)
        fmt = kargs.pop("fmt", "latex" if latex else "text")
        escape = kargs.pop("escape", False)

        try:
            value = float(self[key])
        except (ValueError, TypeError):
            raise KeyError(
                "{} should be a floating point value of the metadata not a {}.".format(key, type(self[key]))
            )
        try:
            error = float(self[key + " err"])
        except (TypeError, KeyError):
            error = float_info.epsilon
        return format_error(value, error, fmt=fmt, mode=mode, units=units, prefix=prefix, scape=escape)
