#!/usr/bin/env python

import numpy as np
import pandas as pd
import nibabel as nib
import argparse as ap
import statsmodels.api as sm
import statsmodels.formula.api as smf
from statsmodels.stats.multitest import multipletests
from joblib import Parallel, delayed
import sys
import warnings
from tfce_mediation.cynumstats import resid_covars, tval_int
from tfce_mediation.pyfunc import calc_sobelz
from scipy.stats import t, norm, linregress
from time import time
import math

#naughty
if not sys.warnoptions:
	warnings.simplefilter("ignore")

#import matplotlib.pyplot as plt
#import matplotlib.mlab as mlab

def run_mm(trunc_data, out_data_array, exog_vars, groupVar, i):
	print i
	try:
		out_data_array = sm.MixedLM(trunc_data, exog_vars, groupVar).fit().resid
	except ValueError:
		print("Error %d" % i)
		out_data_array = np.zeros((len(exog_vars)))
	return out_data_array

def mixedmodelparallel(data_array, exog_vars, groupVar, numV, num_cores):
	resid_data = np.zeros(data_array.shape)
	resid_data = Parallel(n_jobs=num_cores)(delayed(run_mm)((data_array[i,:]+1),resid_data[i,:], exog_vars, groupVar, i) for i in range(numV))
	return np.array(resid_data)

def scalevar(X, demean = True, unitvariance = True):
	if demean:
		X = X - np.mean(X)
	if unitvariance:
		X = np.divide(X, np.std(X))
	return X

def create_exog_mat(var_arr, pdDF, scale = False, intVars = False):
	if scale:
		exog_vars = scalevar(pdDF['%s' % var_arr[0]])
		if len(var_arr) is not 1:
			for preds in range(len(var_arr)-1):
				exog_vars = np.column_stack((exog_vars,scalevar(pdDF['%s' % var_arr[preds+1]])))
	else:
		exog_vars = pdDF['%s' % var_arr[0]]
		if len(var_arr) is not 1:
			for preds in range(len(var_arr)-1):
				exog_vars = np.column_stack((exog_vars,pdDF['%s' % var_arr[preds+1]]))
	return np.array(sm.add_constant(exog_vars))

def russiandolls(group_list, pdDF): # mm assumes equal variances... 
	if len(group_list) == 1:
		print('You should not be here...')
	for i, groupvar in enumerate(group_list):
		if i == 0:
			pdDF['group_list'] = pdDF[group_list[0]].astype(np.str) + "_" +  pdDF[group_list[1]].astype(np.str)
		elif i == 1:
			pass
		else:
			pdDF['group_list'] = pdDF['group_list'].astype(np.str) + "_" +  pdDF[groupvar].astype(np.str)
	return pdDF

# pdCSV[groupVar[0]].isnull()
def omitmissing(pdDF, endog_range, exogenous = None, groups = None):
	isnull_arr = pdDF[pdDF.columns[endog_range[0]]].isnull() * 1
	for i in range(int(endog_range[0]),int(endog_range[1])):
		isnull_arr = np.column_stack((isnull_arr, pdDF[pdDF.columns[int(i+1)]].isnull() * 1))
	if exogenous is not None:
		if exogenous.ndim ==1:
			isnull_arr = np.column_stack((isnull_arr, np.isnan(exogenous)*1))
		else:
			for j in range(exogenous.ndim):
				isnull_arr = np.column_stack((isnull_arr, np.isnan(exogenous[:,j])*1))
	if groups is not None:
		for groupvar in groups:
			isnull_arr = np.column_stack((isnull_arr, pdDF[groupvar].isnull() * 1))
	sum_arr = np.sum(isnull_arr, axis=1)
	print("%d out of %d rows contains no NaNs." % (sum_arr[sum_arr==0].shape[0], sum_arr.shape[0]))
	return pdDF[sum_arr == 0]

def special_calc_sobelz(ta, tb, alg = "aroian"):
	if alg == 'aroian':
		#Aroian variant
		SobelZ = 1/np.sqrt((1/(tb**2))+(1/(ta**2))+(1/(ta**2*tb**2)))
	elif alg == 'sobel':
		#Sobel variant
		SobelZ = 1/np.sqrt((1/(tb**2))+(1/(ta**2)))
	elif alg == 'goodman':
		#Goodman variant
		SobelZ = 1/np.sqrt((1/(tb**2))+(1/(ta**2))-(1/(ta**2*tb**2)))
	else:
		print("Unknown indirect test algorithm")
		exit()
	return SobelZ

def strip_ones(arr): # WHYYYYYYY?
	if arr.shape[1] == 2:
		return arr[:,1]
	else:
		return arr[:,1:]

def find_nearest(array, value, p_array):
	idx = np.searchsorted(array, value, side="left")
#	if idx == len(p_array):
#		return p_array[idx-1]
#	elif math.fabs(value - array[idx-1]) < math.fabs(value - array[idx]):
#		return p_array[idx-1]
#	else:
#		return p_array[idx]
	return p_array[idx-1]

#shape 6,70
def run_permutations(endog_arr, exog_vars, num_perm, stat_arr, uniq_groups = None, return_permutations = False):
	stime = time()
	print("The accuracy is p = 0.05 +/- %.4f" % (2*(np.sqrt(0.05*0.95/num_perm))))
	np.random.seed(int(1000+time()))
	n, num_depv = endog_arr.shape
	k = exog_vars.shape[1]
	maxT_arr = np.zeros((int(k-1), num_perm))

	if uniq_groups is not None:
		unique_blocks = np.unique(uniq_groups)

	for i in xrange(num_perm):
		if uniq_groups is not None:
			index_groups = np.array(range(n))
			for block in unique_blocks:
				s = len(index_groups[uniq_groups == block])
				index_temp = index_groups[uniq_groups == block]
				index_groups[uniq_groups == block] = index_temp[np.random.permutation(s)]
			nx = exog_vars[index_groups]
		else:
			nx = exog_vars[np.random.permutation(list(range(n)))]
		invXX = np.linalg.inv(np.dot(exog_vars.T, exog_vars))
		perm_tvalues = tval_int(nx, invXX, endog_arr, n, k, num_depv)
		perm_tvalues[np.isnan(perm_tvalues)]=0
		maxT_arr[:,i] = perm_tvalues.max(axis=1)[1:]
	corrP_arr = np.zeros_like(stat_arr)
	p_array=np.zeros(num_perm)
	for j in range(num_perm):
		p_array[j] = np.true_divide(j,num_perm)
	for k in range(maxT_arr.shape[0]):
		sorted_maxT = np.sort(maxT_arr[k,:])
		sorted_maxT[sorted_maxT<0]=0
		corrP_arr[k,:] = find_nearest(sorted_maxT,np.abs(stat_arr[k,:]),p_array)
	print("%d permutations took %1.2f seconds." % (num_perm ,(time() - stime)))
	if return_permutations:
		return (1 - corrP_arr), maxT_arr
	else:
		return (1 - corrP_arr)


DESCRIPTION = 'Run linear- and linear-mixed models for now.'

def getArgumentParser(ap = ap.ArgumentParser(description = DESCRIPTION, formatter_class=ap.RawTextHelpFormatter)):
	ap.add_argument("-m", "--statsmodel",
		help="Select the statistical model.",
		choices=['mixedmodel', 'mm', 'linear', 'lm'],
		required=True)
	ap.add_argument("-i", "-i_csv", "--inputcsv",
		help="Edit existing *.tmi file.",
		nargs='+', 
		metavar='*.csv',
		required=True)
	ap.add_argument("-o", "--outstats",
		help="Save stats as a *.csv file.",
		nargs=1,
		metavar='str')
	ap.add_argument("-s_csv", "--savecsv",
		help="Save the merged *.csv file.",
		nargs=1, 
		metavar='*.csv',
		required=False)
	ap.add_argument("-ic", "--indexcolumns",
		help="Select the index column for merging *.csv files. Each value in the column should be unique. Default: %(default)s)",
		nargs=1,
		default=['SubjID'])
	ap.add_argument("-on", "--outputcolumnnames",
		help="Output column names and exits",
		action='store_true')
	ap.add_argument("-exog", "-iv","--exogenousvariables",
		help="Exogenous (independent) variables. Intercept(s) will be included automatically. e.g. -exog {time_h} {weight_lbs}",
		metavar='str',
		nargs='+')
	ap.add_argument("-int", "--twowayinteration",
		help="Interaction of two exogenous (independent) variables. Variables should not be included in -exog. e.g. -int diagnosis genescore",
		metavar = ['str','str'],
		nargs = 2)
	ap.add_argument("-g", "--groupingvariable",
		help="Select grouping variable for mixed model.",
		metavar='str',
		nargs='+')
	ap.add_argument("-med", "--mediation",
		help="Select mediation type and mediation variables. The left and right variables must be not included in exogenouse variabls! e.g., -med {medtype(I|M|Y)} {leftvar} {rightvar}",
		nargs=3,
		metavar=('{I|M|Y}', 'LeftVar', 'RightVar'))
	ap.add_argument("-se", "--scaleexog",
		help="Scale the exogenous/independent variables",
		action='store_true')
	ap.add_argument("-r", "--range",
		help="Select the range of columns for analysis",
		nargs=2,
		metavar='int',
		type=int,
		required=False)
	ap.add_argument("-p", "--parallel",
		help="parallel computing for -sr. -p {int}",
		metavar='int',
		nargs=1)
	ap.add_argument("-rand", "--permutation",
		help="Permutation testing for FWER correction. Must be used with -lm. Block variable(s) can be specficied with -g. -rand {num_perm}",
		metavar='int',
		nargs=1)
	return ap

def run(opts):

	indexCol = opts.indexcolumns[0]

	# read csv(s)
	num_csv=len(opts.inputcsv)
	pdCSV = pd.read_csv(opts.inputcsv[0], delimiter=',', index_col=[indexCol])
	if num_csv > 1:
		for i in range(int(num_csv-1)):
			tempCSV = pd.read_csv(opts.inputcsv[int(i+1)], delimiter=',', index_col=[indexCol])
			pdCSV = pd.concat([pdCSV, tempCSV], axis=1, join_axes=[pdCSV.index])

	# Interaction Variables
	if opts.twowayinteration:
		var1 = scalevar(pdCSV[opts.twowayinteration[0]])
		var2 = scalevar(pdCSV[opts.twowayinteration[1]])
		intvar = var1 * var2
		var1_name = '%s_p' % opts.twowayinteration[0]
		var2_name = '%s_p' % opts.twowayinteration[1]
		intvar_name = '%s.X.%s' % (opts.twowayinteration[0],opts.twowayinteration[1])
		pdCSV[var1_name] = var1
		pdCSV[var2_name] = var2
		pdCSV[intvar_name] = intvar
		opts.exogenousvariables.append(var1_name)
		opts.exogenousvariables.append(var2_name)
		opts.exogenousvariables.append(intvar_name)
		print opts.exogenousvariables


	# output column/variable names.
	if opts.outputcolumnnames:
		for counter, roi in enumerate(pdCSV.columns):
			print("[%d] : %s" % (counter, roi))
		quit()

	# set grouping variables
	if opts.groupingvariable:
		if len(opts.groupingvariable) > 1:
			pdCSV = russiandolls(opts.groupingvariable, pdCSV)
			groupVar = 'group_list'
		else:
			groupVar = opts.groupingvariable[0]

	# stats functions

	if opts.outstats:
		if not opts.range:
			print("Range must be specfied")
			quit()
		elif len(opts.range) != 2:
			print("Range must have start and stop")
			quit()
		else:
			roi_names = []
			t_values = []
			p_values = []
			if not opts.exogenousvariables:
				print("The exogenous (independent) variables must be specifice. e.g., -exog pred1 pred2 age")
				quit()

			if opts.mediation:
				medvars = ['%s' % opts.mediation[1], '%s' % opts.mediation[2]]
				exog_vars = create_exog_mat(opts.exogenousvariables, pdCSV, opts.scaleexog==True)
				# build null array
				pdCSV = omitmissing(pdDF = pdCSV,
								endog_range = opts.range, 
								exogenous = strip_ones(exog_vars),
								groups = medvars)
				if opts.statsmodel == 'mixedmodel' or opts.statsmodel == 'mm':
					pdCSV = omitmissing(pdDF = pdCSV,
									endog_range = opts.range, 
									groups = opts.groupingvariable)
				# rebuild exog_vars with correct length
				exog_vars = create_exog_mat(opts.exogenousvariables, pdCSV, opts.scaleexog==True)
				leftvar = pdCSV[opts.mediation[1]]
				rightvar = pdCSV[opts.mediation[2]]
				y = pdCSV.iloc[:,int(opts.range[0]):int(opts.range[1])+1]

				if opts.statsmodel == 'mixedmodel' or opts.statsmodel == 'mm':
					t_valuesA = []
					t_valuesB = []

					if opts.mediation[0] == 'I':
						EXOG_A = sm.add_constant(np.column_stack((leftvar, strip_ones(exog_vars))))
						EXOG_B = np.column_stack((leftvar, rightvar))
						EXOG_B = sm.add_constant(np.column_stack((EXOG_B, strip_ones(exog_vars))))
						#pathA
						for i in xrange(int(opts.range[0]),int(opts.range[1])+1):
							mdl_fit = sm.MixedLM(pdCSV[pdCSV.columns[i]], EXOG_A, pdCSV[groupVar]).fit()
							roi_names.append(pdCSV.columns[i])
							t_valuesA.append(mdl_fit.tvalues[1])
						#pathB
						for i in xrange(int(opts.range[0]),int(opts.range[1])+1):
							mdl_fit = sm.MixedLM(pdCSV[pdCSV.columns[i]], EXOG_B, pdCSV[groupVar]).fit()
							t_valuesB.append(mdl_fit.tvalues[1])
					elif opts.mediation[0] == 'M':
						EXOG_A = sm.add_constant(np.column_stack((leftvar, strip_ones(exog_vars))))
						EXOG_B = np.column_stack((rightvar, leftvar))
						EXOG_B = sm.add_constant(np.column_stack((EXOG_B, strip_ones(exog_vars))))
						#pathA
						for i in xrange(int(opts.range[0]),int(opts.range[1])+1):
							mdl_fit = sm.MixedLM(pdCSV[pdCSV.columns[i]], EXOG_A, pdCSV[groupVar]).fit()
							roi_names.append(pdCSV.columns[i])
							t_valuesA.append(mdl_fit.tvalues[1])
						#pathB
						for i in xrange(int(opts.range[0]),int(opts.range[1])+1):
							mdl_fit = sm.MixedLM(pdCSV[pdCSV.columns[i]], EXOG_B, pdCSV[groupVar]).fit()
							t_valuesB.append(mdl_fit.tvalues[1])
					else:
						EXOG_A = sm.add_constant(np.column_stack((leftvar, strip_ones(exog_vars))))
						EXOG_B = np.column_stack((rightvar, leftvar))
						EXOG_B = sm.add_constant(np.column_stack((EXOG_B, strip_ones(exog_vars))))

						#pathA
						mdl_fit = sm.MixedLM(rightvar, EXOG_A, pdCSV[groupVar]).fit()
						t_valuesA = mdl_fit.tvalues[1]

						#pathB
						for i in xrange(int(opts.range[0]),int(opts.range[1])+1):
							mdl_fit = sm.MixedLM(pdCSV[pdCSV.columns[i]], exog_vars, pdCSV[groupVar]).fit()
							roi_names.append(pdCSV.columns[i])
							t_valuesB.append(mdl_fit.tvalues[1])


					z_values  = special_calc_sobelz(np.array(t_valuesA), np.array(t_valuesB), alg = "aroian")
					p_values = norm.sf(abs(z_values))
					p_FDR = multipletests(p_values, method = 'fdr_bh')[1]

				else:
					#LM mediation
					if opts.mediation[0] == 'I':
						EXOG_A = sm.add_constant(np.column_stack((leftvar, strip_ones(exog_vars))))
						EXOG_B = np.column_stack((leftvar, rightvar))
						EXOG_B = sm.add_constant(np.column_stack((EXOG_B, strip_ones(exog_vars))))

						#pathA
						invXX = np.linalg.inv(np.dot(EXOG_A.T, EXOG_A))
						y = pdCSV.iloc[:,int(opts.range[0]):int(opts.range[1])+1]
						n, num_depv = y.shape
						k = EXOG_A.shape[1]
						t_valuesA = tval_int(EXOG_A, invXX, y, n, k, num_depv)[1,:]

						#pathB
						invXX = np.linalg.inv(np.dot(EXOG_B.T, EXOG_B))
						y = pdCSV.iloc[:,int(opts.range[0]):int(opts.range[1])+1]
						n, num_depv = y.shape
						k = EXOG_B.shape[1]
						t_valuesB = tval_int(EXOG_B, invXX, y, n, k, num_depv)[1,:]

					elif opts.mediation[0] == 'M':
						EXOG_A = sm.add_constant(np.column_stack((leftvar, strip_ones(exog_vars))))
						EXOG_B = np.column_stack((rightvar, leftvar))
						EXOG_B = sm.add_constant(np.column_stack((EXOG_B, strip_ones(exog_vars))))

						#pathA
						invXX = np.linalg.inv(np.dot(EXOG_A.T, EXOG_A))
						y = pdCSV.iloc[:,int(opts.range[0]):int(opts.range[1])+1]
						n, num_depv = y.shape
						k = EXOG_A.shape[1]
						t_valuesA = tval_int(EXOG_A, invXX, y, n, k, num_depv)[1,:]

						#pathB
						invXX = np.linalg.inv(np.dot(EXOG_B.T, EXOG_B))
						y = pdCSV.iloc[:,int(opts.range[0]):int(opts.range[1])+1]
						n, num_depv = y.shape
						k = EXOG_B.shape[1]
						t_valuesB = tval_int(EXOG_B, invXX, y, n, k, num_depv)[1,:]
					elif opts.mediation[0] == 'Y':
						EXOG_A = sm.add_constant(np.column_stack((leftvar, strip_ones(exog_vars))))
						EXOG_B = np.column_stack((rightvar, leftvar))
						EXOG_B = sm.add_constant(np.column_stack((EXOG_B, strip_ones(exog_vars))))

						#pathA
						mdl_fit = sm.OLS(rightvar, EXOG_A).fit()
						t_valuesA = mdl_fit.tvalues[1]

						#pathB
						invXX = np.linalg.inv(np.dot(EXOG_B.T, EXOG_B))
						y = pdCSV.iloc[:,int(opts.range[0]):int(opts.range[1])+1]
						n, num_depv = y.shape
						k = EXOG_B.shape[1]
						t_valuesB = tval_int(EXOG_B, invXX, y, n, k, num_depv)[1,:]
					else:
						print("Error: Invalid mediation type.")
						quit()
					z_values  = special_calc_sobelz(np.array(t_valuesA), np.array(t_valuesB), alg = "aroian")
					p_values = norm.sf(abs(z_values))
					p_FDR = multipletests(p_values, method = 'fdr_bh')[1]

					roi_names = []
					for i in xrange(int(opts.range[0]),int(opts.range[1])+1):
						roi_names.append(pdCSV.columns[i])

				columnnames = []
				columnnames.append('Zval')
				columnnames.append('pval')
				columnnames.append('pFDR')
				columndata = np.column_stack((z_values, p_values))
				columndata = np.column_stack((columndata, p_FDR))
				pd_DF = pd.DataFrame(data=columndata, index=roi_names, columns=columnnames)
				pd_DF.to_csv(opts.outstats[0], index_label='ROI')

			else:
				# MIXED MODEL
				if opts.statsmodel == 'mixedmodel' or opts.statsmodel == 'mm':
					exog_vars = create_exog_mat(opts.exogenousvariables, pdCSV, opts.scaleexog==True)

					# build null array
					pdCSV = omitmissing(pdDF = pdCSV,
									endog_range = opts.range, 
									exogenous = strip_ones(exog_vars),
									groups = opts.groupingvariable)
					# rebuild exog_vars with correct length
					exog_vars = create_exog_mat(opts.exogenousvariables, pdCSV, opts.scaleexog==True)

					for i in xrange(int(opts.range[0]),int(opts.range[1])+1):
						mdl_fit = sm.MixedLM(pdCSV[pdCSV.columns[i]], exog_vars, pdCSV[groupVar]).fit()
						roi_names.append(pdCSV.columns[i])
						t_values.append(mdl_fit.tvalues[1:])
						p_values.append(mdl_fit.pvalues[1:])

					p_values = np.array(p_values)
					t_values = np.array(t_values)
					p_FDR = np.zeros_like(p_values)

					p_values[np.isnan(p_values)]=1
					for col in range(p_FDR.shape[1]):
						p_FDR[:,col] = multipletests(p_values[:,col], method = 'fdr_bh')[1]

					columnnames = []
					for colname in opts.exogenousvariables:
						columnnames.append('tval_%s' % colname)
					columnnames.append('tval_groupRE')
					for colname in opts.exogenousvariables:
						columnnames.append('pval_%s' % colname)
					columnnames.append('pval_groupRE')
					for colname in opts.exogenousvariables:
						columnnames.append('pFDR_%s' % colname)
					columnnames.append('pFDR_groupRE')
					columndata = np.column_stack((t_values, p_values))
					columndata = np.column_stack((columndata, p_FDR))
					pd_DF = pd.DataFrame(data=columndata, index=roi_names, columns=columnnames)
					pd_DF.to_csv(opts.outstats[0], index_label='ROI')
				else:
					# LINEAR MODEL
					exog_vars = create_exog_mat(opts.exogenousvariables, pdCSV, opts.scaleexog==True)
					# build null array
					pdCSV = omitmissing(pdDF = pdCSV,
									endog_range = opts.range,
									exogenous = strip_ones(exog_vars))
					# rebuild exog_vars with correct length
					exog_vars = create_exog_mat(opts.exogenousvariables, pdCSV, opts.scaleexog==True)
					invXX = np.linalg.inv(np.dot(exog_vars.T, exog_vars))
					y = pdCSV.iloc[:,int(opts.range[0]):int(opts.range[1])+1]
					n, num_depv = y.shape
					k = exog_vars.shape[1]
					t_values = tval_int(exog_vars, invXX, y, n, k, num_depv)[1:,:]
					p_values = t.sf(np.abs(t_values), n-1)*2
					if opts.permutation:
						if opts.groupingvariable:
							p_FWER = run_permutations(endog_arr = y,
								exog_vars = exog_vars,
								num_perm = int(opts.permutation[0]),
								stat_arr = t_values,
								uniq_groups = pdCSV[groupVar],
								return_permutations = False)
						else:
							p_FWER = run_permutations(endog_arr = y,
								exog_vars = exog_vars,
								num_perm = int(opts.permutation[0]),
								stat_arr = t_values,
								uniq_groups = None,
								return_permutations = False)
						p_FWER = p_FWER.T

					t_values = t_values.T
					p_values = p_values.T

					roi_names = []
					for i in xrange(int(opts.range[0]),int(opts.range[1])+1):
						roi_names.append(pdCSV.columns[i])

					p_FDR = np.zeros_like(p_values)
					p_values[np.isnan(p_values)]=1
					for col in range(p_FDR.shape[1]):
						p_FDR[:,col] = multipletests(p_values[:,col], method = 'fdr_bh')[1]

					columnnames = []
					for colname in opts.exogenousvariables:
						columnnames.append('tval_%s' % colname)
					for colname in opts.exogenousvariables:
						columnnames.append('pval_%s' % colname)
					for colname in opts.exogenousvariables:
						columnnames.append('pFDR_%s' % colname)
					if opts.permutation:
						for colname in opts.exogenousvariables:
							columnnames.append('pFWER_%s' % colname)
					columndata = np.column_stack((t_values, p_values))
					columndata = np.column_stack((columndata, p_FDR))
					if opts.permutation:
						columndata = np.column_stack((columndata, p_FWER))
					pd_DF = pd.DataFrame(data=columndata, index=roi_names, columns=columnnames)
					pd_DF.to_csv(opts.outstats[0], index_label='ROI')


	if opts.savecsv:
		pdCSV.to_csv(opts.savecsv[0])

if __name__ == "__main__":
	parser = getArgumentParser()
	opts = parser.parse_args()
	run(opts)