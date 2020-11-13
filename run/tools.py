import os
import argparse
import pickle
import json
import numpy as np
import pandas as pd
from tqdm import tqdm
tqdm.pandas()
from rdkit import Chem
from rdkit.Chem import rdChemReactions
from chemml.graph.hashgraph import HashGraph
from chemml.graph.from_rdkit import rdkit_config
from chemml.graph.substructure import AtomEnvironment
from chemml.kernels.ConvKernel import *


def set_graph_property(input_config):
    single_graph, multi_graph, r_graph, properties = input_config.split(':')
    single_graph = single_graph.split(',') if single_graph else []
    multi_graph = multi_graph.split(',') if multi_graph else []
    reaction_graph = r_graph.split(',') if r_graph else []
    properties = properties.split(',')
    return single_graph, multi_graph, reaction_graph, properties


def set_block_config(block_config):
    block_length = int(block_config.split(':')[0])
    block_x_id = int(block_config.split(':')[1].split(',')[0])
    block_y_id = int(block_config.split(':')[1].split(',')[1])
    return block_length, block_x_id, block_y_id


def set_gpr_optimizer(gpr):
    gpr, optimizer = gpr.split(':')
    if gpr not in ['graphdot', 'sklearn']:
        raise Exception('Unknown gpr')
    if optimizer in ['None', 'none', '']:
        return gpr, None
    if gpr == 'graphdot' and optimizer != 'L-BFGS-B':
        raise Exception('Please use L-BFGS-B optimizer')
    return gpr, optimizer


def set_kernel_alpha(kernel):
    kernel, alpha = kernel.split(':')
    if kernel not in ['graph', 'preCalc']:
        raise Exception('Unknown kernel')
    return kernel, float(alpha)


def set_add_feature_hyperparameters(add_features):
    if add_features is None:
        return None, None
    add_f, add_p = add_features.split(':')
    add_f = add_f.split(',')
    add_p = list(map(float, add_p.split(',')))
    assert(len(add_f) == len(add_p))
    return add_f, add_p


def set_mode_train_size_ratio_seed(train_test_config):
    result = train_test_config.split(':')
    if len(result) == 4:
        mode, train_size, train_ratio, seed = result
        dynamic_train_size = 0
    else:
        mode, train_size, train_ratio, seed, dynamic_train_size = result
    train_size = int(train_size) if train_size else None
    train_ratio = float(train_ratio) if train_ratio else None
    seed = int(seed) if seed else 0
    dynamic_train_size = int(dynamic_train_size) if dynamic_train_size else 0
    return mode, train_size, train_ratio, seed, dynamic_train_size


def set_learner(gpr):
    if gpr == 'graphdot':
        from chemml.GPRgraphdot.learner import Learner
    elif gpr == 'sklearn':
        from chemml.GPRsklearn.learner import Learner
    else:
        raise Exception('Unknown GaussianProcessRegressor: %s' % gpr)
    return Learner


def set_gpr(gpr):
    if gpr == 'graphdot':
        from chemml.GPRgraphdot.gpr import GPR as GaussianProcessRegressor
    elif gpr == 'sklearn':
        from chemml.GPRsklearn.gpr import RobustFitGaussianProcessRegressor as \
            GaussianProcessRegressor
    else:
        raise Exception('Unknown GaussianProcessRegressor: %s' % gpr)
    return GaussianProcessRegressor


def set_active_config(active_config):
    learning_mode, add_mode, init_size, add_size, max_size, search_size, \
    pool_size, stride = active_config.split(':')
    init_size = int(init_size) if init_size else 0
    add_size = int(add_size) if add_size else 0
    max_size = int(max_size) if max_size else 0
    search_size = int(search_size) if search_size else 0
    pool_size = int(pool_size) if pool_size else 0
    stride = int(stride) if stride else 0
    return learning_mode, add_mode, init_size, add_size, max_size, \
           search_size, pool_size, stride


def set_kernel_config(kernel, add_features, add_hyperparameters,
                      single_graph, multi_graph, hyperjson,
                      result_dir):
    if kernel == 'graph':
        hyperdict = [
            json.loads(open(f, 'r').readline()) for f in hyperjson.split(',')
        ]
        params = {
            'single_graph': single_graph,
            'multi_graph': multi_graph,
            'hyperdict': hyperdict
        }
        from chemml.kernels.GraphKernel import GraphKernelConfig as KConfig
    else:
        params = {
            'result_dir': result_dir,
        }
        from chemml.kernels.PreCalcKernel import PreCalcKernelConfig as KConfig
    return KConfig(add_features, add_hyperparameters, params)


def read_input(result_dir, input, kernel_config, properties, params):
    def df_filter(df, train_size=None, train_ratio=None, bygroup=False, seed=0):
        np.random.seed(seed)
        if bygroup:
            gname = 'group_id'
        else:
            gname = 'id'
        unique_ids = df[gname].unique()
        if train_size is None:
            train_size = int(unique_ids.size * train_ratio)
        ids = np.random.choice(unique_ids, train_size, replace=False)
        df_train = df[df[gname].isin(ids)]
        df_test = df[~df[gname].isin(ids)]
        return df_train, df_test

    if params is None:
        params = {
            'train_size': None,
            'train_ratio': 1.0,
            'seed': 0,
        }
    print('***\tStart: Reading input.\t***')
    if not os.path.exists(result_dir):
        os.mkdir(result_dir)
    # read input.
    single_graph = kernel_config.single_graph \
        if hasattr(kernel_config, 'single_graph') else []
    multi_graph = kernel_config.multi_graph \
        if hasattr(kernel_config, 'multi_graph') else []
    df = get_df(input,
                os.path.join(result_dir, '%s.pkl' % ','.join(properties)),
                single_graph, multi_graph, [])
    # get df of train and test sets
    df_train, df_test = df_filter(
        df,
        train_size=params['train_size'],
        train_ratio=params['train_ratio'],
        seed=params['seed'],
        bygroup=kernel_config.add_features is not None
    )
    # get X, Y of train and test sets
    train_X, train_Y, train_id = get_XYid_from_df(
        df_train,
        kernel_config,
        properties=properties,
    )
    test_X, test_Y, test_id = get_XYid_from_df(
        df_test,
        kernel_config,
        properties=properties,
    )
    if test_X is None:
        test_X = train_X
        test_Y = np.copy(train_Y)
        test_id = train_id
    print('***\tEnd: Reading input.\t***\n')
    return (df, df_train, df_test, train_X, train_Y, train_id, test_X,
            test_Y, test_id)


def gpr_run(data, result_dir, kernel_config, params,
            load_model=False, tag=0):
    df = data['df']
    df_train = data['df_train']
    train_X = data['train_X']
    train_Y = data['train_Y']
    train_id = data['train_id']
    test_X = data['test_X']
    test_Y = data['test_Y']
    test_id = data['test_id']
    optimizer = params['optimizer']
    mode = params['mode']
    alpha = params['alpha']
    Learner = params['Learner']
    dynamic_train_size = params['dynamic_train_size']

    # pre-calculate graph kernel matrix.
    '''
    if params['optimizer'] is None:
        pre_calculate(kernel_config, df, result_dir, load_K)
    '''

    print('***\tStart: hyperparameters optimization.\t***')
    if mode == 'loocv':  # directly calculate the LOOCV
        learner = Learner(train_X, train_Y, train_id, test_X, test_Y,
                          test_id, kernel_config, alpha=alpha,
                          optimizer=optimizer)
        if load_model:
            print('loading existed model')
            learner.model.load(result_dir)
        else:
            learner.train()
            learner.model.save(result_dir)
            learner.kernel_config.save(result_dir, learner.model)
        r2, ex_var, mse, mae, out = learner.evaluate_loocv()
        print('LOOCV:')
        print('score: %.5f' % r2)
        print('explained variance score: %.5f' % ex_var)
        print('mse: %.5f' % mse)
        print('mae: %.5f' % mae)
        out.to_csv('%s/loocv.log' % result_dir, sep='\t', index=False,
                   float_format='%15.10f')
    elif mode == 'dynamic':
        learner = Learner(train_X, train_Y, train_id, test_X, test_Y,
                          test_id, kernel_config, alpha=alpha,
                          optimizer=optimizer)
        r2, ex_var, mse, mae, out = learner.evaluate_test_dynamic(
            dynamic_train_size=dynamic_train_size)
        print('Test set:')
        print('score: %.5f' % r2)
        print('explained variance score: %.5f' % ex_var)
        print('mse: %.5f' % mse)
        print('mae: %.5f' % mae)
        out.to_csv('%s/test-%i.log' % (result_dir, tag), sep='\t', index=False,
                   float_format='%15.10f')
    else:
        learner = Learner(train_X, train_Y, train_id, test_X, test_Y,
                          test_id, kernel_config, alpha=alpha,
                          optimizer=optimizer)
        learner.train()
        learner.model.save(result_dir)
        learner.kernel_config.save(result_dir, learner.model)
        print('***\tEnd: hyperparameters optimization.\t***\n')
        r2, ex_var, mse, mae, out = learner.evaluate_train()
        print('Training set:')
        print('score: %.5f' % r2)
        print('explained variance score: %.5f' % ex_var)
        print('mse: %.5f' % mse)
        print('mae: %.5f' % mae)
        out.to_csv('%s/train-%i.log' % (result_dir, tag), sep='\t', index=False,
                   float_format='%15.10f')
        r2, ex_var, mse, mae, out = learner.evaluate_test()
        print('Test set:')
        print('score: %.5f' % r2)
        print('explained variance score: %.5f' % ex_var)
        print('mse: %.5f' % mse)
        print('mae: %.5f' % mae)
        out.to_csv('%s/test-%i.log' % (result_dir, tag), sep='\t', index=False,
                   float_format='%15.10f')

def _get_uniX(X):
    return np.sort(np.unique(X))


def get_df(csv, pkl, single_graph, multi_graph, reaction_graph):
    def single2graph(series):
        unique_series = _get_uniX(series)
        graphs = list(map(HashGraph.from_inchi_or_smiles, unique_series,
                          [rdkit_config()] * len(unique_series),
                          series['group_id']))
        unify_datatype(graphs)
        idx = np.searchsorted(unique_series, series)
        return np.asarray(graphs)[idx]

    def multi_graph_transform(line, hash):
        hashs = [str(hash) + '_%d' % i for i in range(int(len(line)/2))]
        line[::2] = list(map(HashGraph.from_inchi_or_smiles, line[::2],
                             [rdkit_config()] * int(len(line) / 2),
                             hashs))
        return line

    def reaction2agent(reaction_smarts, hash):
        agents = []
        rxn = rdChemReactions.ReactionFromSmarts(reaction_smarts)
        # print(reaction_smarts)
        for i, mol in enumerate(rxn.GetAgents()):
            Chem.SanitizeMol(mol)
            hash_ = hash + '_%d' % i
            config_ = rdkit_config()
            agents += [HashGraph.from_rdkit(mol, config_, hash_), 1.0]
        return agents

    def reaction2rp(reaction_smarts, hash):
        reaction = []
        rxn = rdChemReactions.ReactionFromSmarts(reaction_smarts)

        # rxn.Initialize()
        def getAtomMapDict(mols):
            AtomMapDict = dict()
            for mol in mols:
                Chem.SanitizeMol(mol)
                for atom in mol.GetAtoms():
                    AMN = atom.GetPropsAsDict().get('molAtomMapNumber')
                    if AMN is not None:
                        AtomMapDict[AMN] = AtomEnvironment(
                            mol, atom, depth=1)
            return AtomMapDict

        def getReactingAtoms(rxn):
            ReactingAtoms = []
            reactantAtomMap = getAtomMapDict(rxn.GetReactants())
            productAtomMap = getAtomMapDict(rxn.GetProducts())
            for id, AE in reactantAtomMap.items():
                if AE != productAtomMap.get(id):
                    ReactingAtoms.append(id)
            return ReactingAtoms

        ReactingAtoms = getReactingAtoms(rxn)
        for i, reactant in enumerate(rxn.GetReactants()):
            Chem.SanitizeMol(reactant)
            hash_ = hash + '_r%d' % i
            config_ = rdkit_config(reaction_center=ReactingAtoms)
            reaction += [HashGraph.from_rdkit(reactant, config_, hash_), 1.0]
            if True not in reaction[-2].nodes.to_pandas()['group_reaction']:
                raise Exception('Reactants error:', reaction_smarts)
        for i, product in enumerate(rxn.GetProducts()):
            Chem.SanitizeMol(product)
            hash_ = hash + '_p%d' % i
            config_ = rdkit_config(reaction_center=ReactingAtoms)
            reaction += [HashGraph.from_rdkit(product, config_, hash_), -1.0]
            if True not in reaction[-2].nodes.to_pandas()['group_reaction']:
                raise Exception('Products error:', reaction_smarts)
        return reaction

    if pkl is not None and os.path.exists(pkl):
        print('reading existing pkl file: %s' % pkl)
        df = pd.read_pickle(pkl)
    else:
        df = pd.read_csv(csv, sep='\s+', header=0)
        if 'id' not in df:
            df['id'] = df.index + 1
            df['group_id'] = df['id']
        else:
            groups = df.groupby(single_graph + multi_graph + reaction_graph)
            df['group_id'] = 0
            for g in groups:
                g[1]['group_id'] = int(g[1]['id'].min())
                df.update(g[1])
            df['id'] = df['id'].astype(int)
            df['group_id'] = df['group_id'].astype(int)
        for sg in single_graph:
            print('Processing single graph.')
            if len(np.unique(df[sg])) > 0.5 * len(df[sg]):
                df[sg] = df.progress_apply(
                    lambda x: HashGraph.from_inchi_or_smiles(
                        x[sg], rdkit_config(), str(x['group_id'])), axis=1)
                unify_datatype(df[sg])
            else:
                df[sg] = single2graph(df[sg])
        for mg in multi_graph:
            print('Processing multi graph.')
            df[mg] = df.progress_apply(
                lambda x: multi_graph_transform(
                    x[mg], str(x['group_id'])), axis=1)
            unify_datatype(df[mg])

        for rg in reaction_graph:
            print('Processing reagents graph.')
            print(df[rg])
            df[rg + '_agents'] = df.progress_apply(
                lambda x: reaction2agent(x[rg], str(x['group_id'])), axis=1)
            unify_datatype(df[rg + '_agents'])
            print('Processing reactions graph.')
            df[rg] = df.progress_apply(
                lambda x: reaction2rp(x[rg], str(x['group_id'])), axis=1)
            unify_datatype(df[rg])
        if pkl is not None:
            df.to_pickle(pkl)
    return df


def get_XYid_from_df(df, kernel_config, properties=None):
    if df.size == 0:
        return None, None, None
    if kernel_config.type == 'graph':
        X_name = kernel_config.single_graph + kernel_config.multi_graph
    elif kernel_config.type == 'preCalc':
        X_name = ['group_id']
    else:
        raise Exception('unknown kernel type:', kernel_config.type)
    if kernel_config.add_features is not None:
        X_name += kernel_config.add_features
    X = df[X_name].to_numpy()
    if properties is None:
        return X, None, None
    Y = df[properties].to_numpy()
    if len(properties) == 1:
        Y = Y.ravel()
    return X, Y, df['id'].to_numpy()


def get_Xgroupid_from_df(df, single_graph, multi_graph):
    if df.size == 0:
        return None, None
    X_name = single_graph + multi_graph
    df_ = []
    for x in df.groupby('group_id'):
        for name in X_name:
            assert (len(np.unique(x[1][name])) == 1)
        df_.append(x[1].sample(1))
    df_ = pd.concat(df_)
    return df_[X_name].to_numpy(), df_['group_id'].to_numpy()


def unify_datatype(X):
    if X[0].__class__ == list:
        graphs = []
        for x in X:
            graphs += x[::2]
        HashGraph.unify_datatype(graphs, inplace=True)
    else:
        HashGraph.unify_datatype(X, inplace=True)