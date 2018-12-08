"""
Unit handling for CellML models, using the Pint unit library (replaces previous
Sympy-units implementation
"""
import logging
import math

import pint
import sympy
from pint.quantity import _Quantity as Quantity
from pint.unit import _Unit as Unit
from sympy.printing.lambdarepr import LambdaPrinter


logging.basicConfig(level=logging.DEBUG)

# The full list of supported CellML units
# Taken from https://www.cellml.org/specifications/cellml_1.1/#sec_units
# Some are not defined by Sympy, see comments
CELLML_UNITS = {
    # Base SI units
    'ampere',
    'candela',
    'kelvin',
    'kilogram',
    'meter',
    'mole',
    'second',

    # Derived SI units
    'becquerel',
    'celsius',
    'coulomb',
    'farad',
    'gray',
    'henry',
    'hertz',
    'joule',
    'katal',  # see __add_custom_units()
    'lumen',
    'lux',
    'newton',
    'ohm',
    'pascal',
    'radian',
    'siemens',
    'sievert',
    'steradian',
    'tesla',
    'volt',
    'watt',
    'weber',

    # Convenience units
    'dimensionless',
    'gram',
    'liter',

    # Aliases
    'metre',
    'litre',
}


class UnitStore(object):

    def __init__(self, cellml_def=None):
        """Initialise a QuantityStore instance
        :param cellml_def: a dictionary of <units> definitions from the CellML model. See parser
        for format, essentially: {'name of unit': { [ unit attributes ], [ unit attributes ] } }
        """
        # Initialise the unit registry
        # TODO: create Pint unit definition file for CellML
        self.ureg: pint.UnitRegistry = pint.UnitRegistry()

        # Add default CellML units not provided by Pint
        self.__add_undefined_units()

        # Hold on to custom unit definitions
        self.cellml_definitions = cellml_def if cellml_def else {}
        self.cellml_defined = set()

    def __add_undefined_units(self):
        """Adds units required by CellML but not provided by Pint."""
        self.ureg.define('katal = mol / second = kat')

    def get_quantity(self, unit_name: str):
        """Given the name of the unit, this will either (i) return a Quantity from the internal
        store as its already been resolved previously (ii) return the Quantity from Pint if it a
        built-in name or (iii) construct and return a new Quantity object using the
        <units><unit></units> definition in the CellML <model>
        """

        # If this unit is a custom CellML definition and we haven't defined it
        if unit_name in self.cellml_definitions and unit_name not in self.cellml_defined:
            # TODO create cellml pint unit definitions file
            try:
                getattr(self.ureg, unit_name)
            except pint.UndefinedUnitError:
                # Create the unit definition and add to the unit registry
                unit_definition = self._make_cellml_unit(unit_name)
                self.ureg.define(unit_definition)
                self.cellml_defined.add(unit_name)

        # return the defined unit from the registry
        try:
            resolved_unit = self.ureg.parse_expression(unit_name).units
            return resolved_unit
        except pint.UndefinedUnitError:
            raise ValueError('Cannot find the unit with name "%s"' % unit_name)

    def _make_cellml_unit(self, custom_unit_name):
        """Uses the CellML definition for 'unit_name' to construct a Pint unit definition string
        """
        full_unit_expr = []

        # For each of the <unit> elements for this unit definition
        for unit_element in self.cellml_definitions[custom_unit_name]:
            # TODO: handle 'base_units'

            # Source the <unit units="XXX"> from our store
            matched_unit = self.get_quantity(unit_element['units'])

            # Construct a string representing the expression for this <unit>
            expr = str(matched_unit)

            if 'prefix' in unit_element:
                expr = '%s%s' % (unit_element['prefix'], expr)

            if 'exponent' in unit_element:
                expr = '((%s)**%s)' % (expr, unit_element['exponent'])

            # Collect/add this particular <unit> definition
            full_unit_expr.append(expr)

        # Join together all the parts of the unit expression
        full_unit_expr = '*'.join(full_unit_expr)

        # Return Pint definition string
        logging.debug('Unit %s => %s', custom_unit_name, full_unit_expr)
        return '%s = %s' % (custom_unit_name, full_unit_expr)

    @staticmethod
    def one_of_unit(unit):
        assert isinstance(unit, Unit)
        return 1 * unit

    @staticmethod
    def is_unit_equal(u1, u2):
        q1 = u1 if isinstance(u1, Quantity) else UnitStore.one_of_unit(u1)
        q2 = u2 if isinstance(u2, Quantity) else UnitStore.one_of_unit(u2)
        is_equal = q1.dimensionality == q2.dimensionality and math.isclose(q1.to(q2).magnitude,
                                                                           q1.magnitude)
        logging.debug('is_unit_equal(%s, %s) -> %s', q1.units, q2.units, is_equal)
        return is_equal

    @staticmethod
    def is_quantity_equal(q1, q2):
        assert isinstance(q1, Quantity)
        assert isinstance(q2, Quantity)
        return q1.dimensionality == q2.dimensionality and q1.magnitude == q2.magnitude

    @staticmethod
    def convert_to(q1, q2):
        q1 = q1 if isinstance(q1, Quantity) else UnitStore.one_of_unit(q1)
        q2 = q2 if isinstance(q2, Quantity) else UnitStore.one_of_unit(q2)
        return q1.to(q2)

    def summarise_units(self, expr: sympy.Expr):
        """Given a Sympy expression, will get the lambdified string to evaluate units
        """
        printer = UnitLambdaPrinter()

        to_evaluate = printer.doprint(expr)

        to_evaluate = to_evaluate.replace('exp(', 'math.exp(')
        simplified = eval(to_evaluate, {'u': self.ureg, 'math': math}).units
        logging.debug('summarise_units(%s) -> %s -> %s', expr, to_evaluate, simplified)
        return simplified

    @staticmethod
    def get_conversion_factor(from_unit, to_unit):
        assert isinstance(from_unit, Unit)
        assert isinstance(to_unit, Unit)

        assert from_unit.dimensionality == to_unit.dimensionality

        from_quantity = 1 * from_unit
        to_quantity = 1 * to_unit

        return from_quantity.to(to_quantity).magnitude


class UnitDummy(sympy.Dummy):
    def __init__(self, name):
        super().__init__()
        self._unit = None
        self._number = None

    @property
    def unit(self):
        return self._unit

    @unit.setter
    def unit(self, value):
        if value is not None:
            assert isinstance(value, Unit)
        self._unit = value

    @property
    def quantity(self):
        return 1 * self.unit

    @property
    def number(self):
        return self._number

    @number.setter
    def number(self, value):
        if value is not None:
            assert isinstance(value, sympy.Number)
        self._number = value

    def __str__(self, printer=None):
        import re
        if printer and isinstance(printer, UnitLambdaPrinter):
            unit_with_prefix = re.sub(r'\b([a-zA-Z_0-9]+)\b', r'u.\1', str(self.unit))
            return '(1 * (%s))' % unit_with_prefix
        if self._number:
            return '%f[%s]' % (self._number, str(self.unit))
        else:
            return '%s[%s]' % (self.name, str(self.unit))

    __repr__ = __str__
    _sympystr = __str__


class UnitLambdaPrinter(LambdaPrinter):
    def _print_Derivative(self, e):
        state = e.free_symbols.pop()
        free = set(e.canonical_variables.keys()).pop()
        return '(1 * u.%s)/(1 * u.%s)' % (state.unit, free.unit)
