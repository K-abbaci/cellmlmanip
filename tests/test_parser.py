import os

import pytest
import sympy

from cellmlmanip import load_model, parser


class TestParser(object):

    @pytest.fixture(scope="class")
    def model(self):
        """Parses example CellML and returns model"""
        example_cellml = os.path.join(
            os.path.dirname(__file__), "cellml_files", "test_simple_odes.cellml"
        )
        p = parser.Parser(example_cellml)
        model = p.parse()
        return model

    def test_component_count(self, model):
        assert len(model.components) == 21  # grep -c '<component ' test_simple_odes.cellml

    def test_group_relationships(self, model):
        assert model.components['circle_parent'].parent is None

        assert 'circle_x' in model.components['circle_parent'].encapsulated
        assert 'circle_y' in model.components['circle_parent'].encapsulated

        assert 'circle_parent' == model.components['circle_x'].parent
        assert 'circle_parent' == model.components['circle_y'].parent

        assert 'circle_x_source' in model.components['circle_x'].encapsulated
        assert 'circle_x_source' in model.components['circle_x_sibling'].siblings
        assert 'circle_x_sibling' in model.components['circle_x_source'].siblings
        assert 'circle_x' == model.components['circle_x_sibling'].parent

        assert 'circle_y_implementation' not in model.components['circle_parent'].encapsulated

    def test_equations_count(self, model):
        equation_count = 0
        for component in model.components.values():
            equation_count += len(component.equations)
        assert equation_count == 19  # NOTE: determined by eye!

    def test_variable_find(self, model):
        assert model.find_variable({'cmeta_id': 'time'}) == [{'cmeta_id': 'time',
                                                              'name': 'environment$time',
                                                              'public_interface': 'out',
                                                              'units': 'ms',
                                                              '_original_name': 'time',
                                                              '_component_name': 'environment'}]
        matched = model.find_variable({'cmeta_id': 'sv12'})
        assert len(matched) == 1 and \
            matched[0]['dummy'].name == 'single_ode_rhs_const_var$sv1'

    def test_rdf(self, model):
        assert len(model.rdf) == 17

    def test_connections_loaded(self, model):
        assert len(model.connections) == 32  # grep -c '<map_variables ' test_simple_odes.cellml
        first, second = model.connections[0]
        component_1, variable_1 = first
        component_2, variable_2 = second
        var_one = model.components[component_1].variables[variable_1]
        var_two = model.components[component_2].variables[variable_2]
        assert component_1 == 'single_independent_ode'
        assert var_one['name'] == 'single_independent_ode$time'
        assert var_one['public_interface'] == 'in'
        assert component_2 == 'environment'
        assert var_two['name'] == 'environment$time'
        assert var_two['public_interface'] == 'out'

    def test_connections(self, model):
        model.make_connections()
        # Check environment component's time variable has propagated
        environment__time = model.components['environment'].variables['time']['assignment']

        # We're checking sympy.Dummy objects (same name != same hash)
        assert isinstance(environment__time, sympy.Dummy)
        assert environment__time != sympy.Dummy(environment__time.name)

        state_units_conversion2__time = \
            model.components['state_units_conversion2'].variables['time']['assignment']
        assert environment__time == state_units_conversion2__time

        # propagated environment time to inside nested component circle_y
        circle_y__time = model.components['circle_y'].variables['time']['assignment']
        assert environment__time == circle_y__time

        # we have a new equation that links together times in different units
        time_units_conversion2__time = \
            model.components['time_units_conversion2'].variables['time']['assignment']
        equation = sympy.Eq(time_units_conversion2__time, environment__time)
        assert equation in model.components['time_units_conversion2'].equations

    def test_add_units_to_equations(self, model):
        # This is an irreversible operation # TODO: don't mutate?
        model.add_units_to_equations()

        # mV/millisecond == mV_per_ms
        test_equation = model.components['single_independent_ode'].equations[0]
        lhs_units = model.units.summarise_units(test_equation.lhs)
        rhs_units = model.units.summarise_units(test_equation.rhs)
        assert model.units.is_unit_equal(rhs_units, lhs_units)

        # TODO: We should find two equations with different lhs/rhs units
        # 1. time_units_conversion1
        #    Eq(_time_units_conversion1$time, _environment$time) second millisecond
        # 2. time_units_conversion2
        #    Eq(_time_units_conversion2$time, _environment$time) microsecond millisecond
        # Make test to check two are unequal, fix them, then check equal

        # Try fixing all units on the RHS so that they match the LHS
        invalid_rhs_lhs_count = 0
        for component in model.components.values():
            for index, equation in enumerate(component.equations):
                lhs_units = model.units.summarise_units(equation.lhs)
                rhs_units = model.units.summarise_units(equation.rhs)
                if not model.units.is_unit_equal(lhs_units, rhs_units):
                    invalid_rhs_lhs_count += 1
                    new_rhs = model.units.convert_to(1*rhs_units, lhs_units)
                    # Create a new equality with the converted RHS and replace original
                    new_dummy = sympy.Dummy(str(new_rhs.magnitude))
                    model.dummy_info[new_dummy] = {
                        'number': sympy.Float(new_rhs.magnitude),
                        'unit': ((1*lhs_units) / (1*rhs_units)).units
                    }
                    equation = sympy.Eq(equation.lhs, equation.rhs * new_dummy)
                    # Replace the current equation with the same equation multiplied by factor
                    component.equations[index] = equation
                    lhs_units = model.units.summarise_units(equation.lhs)
                    rhs_units = model.units.summarise_units(equation.rhs)
                    # TODO: how to test this?
                    assert model.units.is_unit_equal(lhs_units, rhs_units)
        assert invalid_rhs_lhs_count == 2

    @pytest.mark.skipif('CMLM_TEST_PRINT' not in os.environ, reason="print eq on demand")
    def test_print_eq(self, model):
        from cellmlmanip.units import ExpressionWithUnitPrinter
        printer = ExpressionWithUnitPrinter(symbol_info=model.dummy_info)
        # show equations
        for name, component in model.components.items():
            print('Component: %s' % name)
            for equation in component.equations:
                print('\tEq(%s, %s)' % (printer.doprint(equation.lhs),
                                        printer.doprint(equation.rhs)))
                lhs_units = model.units.summarise_units(equation.lhs)
                rhs_units = model.units.summarise_units(equation.rhs)
                print('\t%s %s %s' %
                      (lhs_units,
                       '==' if model.units.is_unit_equal(rhs_units, lhs_units) else '!=',
                       rhs_units))

    def test_connect_to_hidden_component(self):
        example_cellml = os.path.join(
            os.path.dirname(__file__), "cellml_files", "err_connect_to_hidden_component.cellml"
        )
        p = parser.Parser(example_cellml)

        with pytest.raises(ValueError) as value_info:
            model = p.parse()
            model.make_connections()

        assert 'Cannot determine the source & target' in str(value_info.value)

    def test_bad_connection_units(self):
        example_cellml = os.path.join(
            os.path.dirname(__file__), "cellml_files", "err_bad_connection_units.cellml"
        )
        p = parser.Parser(example_cellml)
        model = p.parse()

        # first we make the connections
        model.make_connections()

        # then add the units to the equations
        model.add_units_to_equations()

        # then check the lhs/rhs units
        with pytest.raises(AssertionError) as assert_info:
            for e in model.equations:
                model.check_left_right_units_equal(e)

        match = ("Units volt (1.0, <Unit('kilogram * meter ** 2 / ampere / second ** 3')>) != "
                 "second (1.0, <Unit('second')>)")
        assert match in str(assert_info.value)

    def test_algebraic(self):
        example_cellml = os.path.join(
            os.path.dirname(__file__), "cellml_files", "algebraic.cellml"
        )
        p = parser.Parser(example_cellml)
        model = p.parse()
        model.make_connections()
        model.add_units_to_equations()
        for e in model.equations:
            model.check_left_right_units_equal(e)

        ureg = model.units.ureg
        assert str(ureg.get_dimensionality(ureg.new_base)) == '[new_base]'
        assert ureg.get_base_units(ureg.new_base/ureg.second) == ureg.get_base_units(ureg.derived)

    def test_undefined_variable(self):
        example_cellml = os.path.join(
            os.path.dirname(__file__), "cellml_files", "undefined_variable.cellml"
        )
        p = parser.Parser(example_cellml)
        with pytest.raises(AssertionError) as assert_info:
            p.parse()

        match = 'c$b not found in symbol dict'
        assert match in str(assert_info.value)

    def test_multiple_math_elements(self):
        example_cellml = os.path.join(
            os.path.dirname(__file__), "cellml_files", "3.4.2.1.component_with_maths.cellml"
        )
        model = load_model(example_cellml)
        assert len(list(model.components['A'].equations)) == 2
        assert len(list(model.equations)) == 2

    def test_new_parser(self):
        example_cellml = os.path.join(
            os.path.dirname(__file__), "cellml_files", "test_simple_odes.cellml"
        )
        model = load_model(example_cellml)
        print('\n\n')
        for k, v in model.variables_x.items():
            print( v)
        for e in model.equations_x:
            print(e)
        for n, a in model.numbers_x.items():
            print(str(a))
