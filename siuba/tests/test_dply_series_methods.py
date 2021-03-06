from siuba.siu import Symbolic, strip_symbolic
from siuba.spec.series import spec
from .helpers import data_frame, assert_equal_query, backend_pandas, SqlBackend, PandasBackend
import pytest
# TODO: dot, corr, cov

from siuba import filter, mutate, summarize, group_by
from pandas.testing import assert_frame_equal, assert_series_equal
import numpy as np
import pandas as pd
import pkg_resources

def filter_on_result(spec, types):
    return [k for k,v in spec.items() if v['result']['type'] in types]

SPEC_IMPLEMENTED = filter_on_result(spec, {"Agg", "Elwise", "Window"})
SPEC_NOTIMPLEMENTED = filter_on_result(spec, {"Singleton"})
SPEC_AGG = filter_on_result(spec, {"Agg"})

_ = Symbolic()

@pytest.fixture(params = tuple(SPEC_IMPLEMENTED))
def entry(request):
    # NOTE: the sole purpose of putting in a fixture is so pytest line output
    #       is very easy to read. (e.g. pytest -v --tb=line)
    key = request.param
    yield spec[key]

@pytest.fixture(params = tuple(SPEC_AGG))
def agg_entry(request):
    key = request.param
    yield spec[key]

@pytest.fixture(params = tuple(SPEC_NOTIMPLEMENTED))
def notimpl_entry(request):
    key  = request.param
    yield spec[key]

def assert_src_array_equal(src, dst):
    if isinstance(src, np.ndarray):
        assert np.array_equal(src, dst)
    elif isinstance(src, pd.DataFrame):
        assert_frame_equal(src, dst)
    elif isinstance(src, pd.Series):
        assert_series_equal(src, dst, check_names = False)
    else:
        assert src == dst
    
# Data ========================================================================
data_dt = data_frame(
    g = ['a', 'a', 'b', 'b'],
    x = pd.to_datetime(["2019-01-01 01:01:01", "2020-04-08 02:02:02", "2021-07-15 03:03:03", "2022-10-22 04:04:04"])
    )

data_str = data_frame(
    g = ['a', 'a', 'b', 'b'],
    x = ['abc', 'cde', 'fg', 'h']
    )

data_bool = data_frame(
    g = ['a', 'a', 'b', 'b'],
    x = [True, False, True, False],
    y = [True, True, False, False]
        )

data_default = data_frame(
    g = ['a', 'a', 'a', 'b', 'b', 'b'],
    x = [10, 11, 11, 13, 13, 13],
    y = [1,2,3,4,5,6]
    )

DATA = data = {
    'dt': data_dt,
    'str': data_str,
    None: data_default,
    'bool': data_bool
}

def get_data(entry, data, backend = None):

    req_bool = entry['result'].get('op') == 'bool'

    # pandas is forgiving to bool inputs
    if isinstance(backend, PandasBackend):
        req_bool = False

    return data['bool'] if req_bool else data[entry['accessor']]


def test_missing_implementation(entry, backend):
    # Check whether test should xfail, skip, or -------------------------------
    backend_status = entry['result'].get(backend.name)

    # case: Needs to be implmented
    # TODO(table): uses xfail
    if backend_status == "xfail":
        pytest.xfail("TODO: impelement this translation")
    
    # case: Can't be used in a mutate (e.g. a SQL ordered set aggregate function)
    # TODO(table): no_mutate
    if backend.name in entry['result'].get('no_mutate', []):
        pytest.skip("Spec'd failure")

    # case: won't be implemented
    if entry['result'].get(backend.name) == "not_impl":
        pytest.skip()


def get_df_expr(entry):
    str_expr = str(entry['expr_frame'])
    call_expr = strip_symbolic(eval(str_expr, {'_': _}))

    return str_expr, call_expr

def cast_result_type(entry, backend, ser):
    sql_type = entry['result'].get('sql_type')
    if isinstance(backend, SqlBackend) and sql_type == 'float':
        return ser.astype('float')
    
    return ser

# Tests =======================================================================


def test_series_against_call(entry):
    if entry['result']['type'] == "Window":
        pytest.skip()

    df = data[entry['accessor']]
    # TODO: once reading from yaml, no need to repr
    str_expr = str(entry['expr_series'])

    call_expr = strip_symbolic(eval(str_expr, {'_': _}))
    res = call_expr(df.x)

    dst = eval(str_expr, {'_': df.x})
    
    assert res.__class__ is dst.__class__
    assert_src_array_equal(res, dst)


def test_frame_expr(entry):
    # TODO: remove this test, not checking anything new
    df = data[entry['accessor']]
    # TODO: once reading from yaml, no need to repr
    str_expr = str(entry['expr_frame'])

    call_expr = strip_symbolic(eval(str_expr, {'_': _}))
    res = call_expr(df)

    dst = eval(str_expr, {'_': df})
    
    assert res.__class__ is dst.__class__
    assert_src_array_equal(res, dst)


def test_pandas_grouped_frame_fast_not_implemented(notimpl_entry):
    from siuba.experimental.pd_groups.dialect import fast_mutate
    gdf = data[notimpl_entry['accessor']].groupby('g')

    # TODO: once reading from yaml, no need to repr
    str_expr = str(notimpl_entry['expr_frame'])
    call_expr = strip_symbolic(eval(str_expr, {'_': _}))

    with pytest.raises(NotImplementedError):
        res = fast_mutate(gdf, result = call_expr)
    


#@backend_pandas
@pytest.mark.skip_backend('sqlite')
def test_frame_mutate(skip_backend, backend, entry):
    test_missing_implementation(entry, backend)

    # Prepare input data ------------------------------------------------------
    # case: inputs must be boolean
    crnt_data = get_data(entry, DATA, backend)
    df = backend.load_df(crnt_data)

    # Execute mutate ----------------------------------------------------------
    str_expr, call_expr = get_df_expr(entry)

    # Run test for equality w/ ungrouped pandas ----
    dst = crnt_data.assign(result = call_expr(crnt_data))
    dst['result'] = cast_result_type(entry, backend, dst['result'])

    assert_equal_query(
            df,
            mutate(result = call_expr),
            dst
            )

    # Run test for equality w/ grouped pandas ----
    g_dst = crnt_data.groupby('g').apply(lambda d: d.assign(result = call_expr)).reset_index(drop = True)
    g_dst['result'] = cast_result_type(entry, backend, g_dst['result'])
    assert_equal_query(
            df,
            group_by(_.g) >> mutate(result = call_expr),
            g_dst
            )


def test_pandas_grouped_frame_fast_mutate(entry):
    from siuba.experimental.pd_groups.dialect import fast_mutate, DataFrameGroupBy
    gdf = get_data(entry, DATA).groupby('g')

    # Execute mutate ----------------------------------------------------------
    str_expr, call_expr = get_df_expr(entry)

    res = fast_mutate(gdf, result = call_expr)
    dst = mutate(gdf, result = call_expr)

    # TODO: apply mark to skip failing tests, rather than downcast
    # pandas grouped aggs, when not using cython, _try_cast back to original type
    # but since mutate uses apply, it doesn't :/. Currently only affects median func.
    dst_obj = dst.obj
    if str_expr == '_.x.median()':
        dst_obj['result'] = gdf._try_cast(dst_obj['result'], gdf.x.obj)

    assert isinstance(dst, DataFrameGroupBy)
    assert_frame_equal(res.obj, dst_obj)


@pytest.mark.skip_backend('sqlite')
def test_frame_summarize(skip_backend, backend, agg_entry):
    entry = agg_entry
    test_missing_implementation(entry, backend)

    # Prepare input data ------------------------------------------------------
    # case: inputs must be boolean
    crnt_data = get_data(entry, DATA, backend)
    df = backend.load_df(crnt_data)

    # Execute mutate ----------------------------------------------------------
    str_expr, call_expr = get_df_expr(entry)

    dst = data_frame(result = call_expr(crnt_data))

    # Process output ----------------------------------------------------------
    # case: output is of a different type than w/ pandas
    dst['result'] = cast_result_type(entry, backend, dst['result'])

    # Run test for equality w/ pandas ----
    # otherwise, verify returns same result as mutate
    assert_equal_query(
            df,
            summarize(result = call_expr),
            dst
            )

    dst_g = crnt_data.groupby('g').apply(call_expr).reset_index().rename(columns = {0: 'result'})
    assert_equal_query(
            df,
            group_by(_.g) >> summarize(result = call_expr),
            dst_g
            )

# Edge Cases ==================================================================

def test_frame_set_aggregates_postgresql():
    # TODO: probably shouldn't be creating backend here
    backend = SqlBackend("postgresql")
    dfs = backend.load_df(data[None])
    
    expr = _.x.quantile(.75)
    assert_equal_query(
            dfs,
            group_by(_.g) >> summarize(result = expr),
            data_frame(g = ['a', 'b'], result = [11., 13.])
            )


