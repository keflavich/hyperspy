# -*- coding: utf-8 -*-
# Copyright 2007-2011 The Hyperspy developers
#
# This file is part of  Hyperspy.
#
#  Hyperspy is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
#  Hyperspy is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with  Hyperspy.  If not, see <http://www.gnu.org/licenses/>.


from __future__ import division
import sys
import os

import numpy as np
import matplotlib.pyplot as plt
import mdp

from hyperspy.misc import utils
from hyperspy.learn.svd_pca import pca
from hyperspy.learn.mlpca import mlpca
from hyperspy.misc.utils import center_and_scale
from hyperspy.defaults_parser import defaults
from hyperspy import messages


class MVA():
    """
    Multivariate analysis capabilities for the Spectrum class.

    """

    def __init__(self):
        self.mva_results = MVA_Results()
        self.peak_chars = None

    def _get_target(self, on_peaks):
        if on_peaks:
            target=self.peak_mva_results
        else:
            target=self.mva_results
        return target

    def principal_components_analysis(self, normalize_poissonian_noise = False,
    algorithm = 'svd', output_dimension = None, navigation_mask = None,
    signal_mask = None, center = False, variance2one = False, var_array = None,
    var_func = None, polyfit = None, on_peaks=False):
        """Principal components analysis.

        The results are stored in self.mva_results

        Parameters
        ----------
        normalize_poissonian_noise : bool
            If True, scale the SI to normalize Poissonian noise
        algorithm : {'svd', 'fast_svd', 'mlpca', 'fast_mlpca', 'mdp', 'NIPALS'}
        output_dimension : None or int
            number of PCA to keep
        navigation_mask : boolean numpy array
        signal_mask : boolean numpy array
        center : bool
            Perform energy centering before PCA
        variance2one : bool
            Perform whitening before PCA
        var_array : numpy array
            Array of variance for the maximum likelihood PCA algorithm
        var_func : function or numpy array
            If function, it will apply it to the dataset to obtain the
            var_array. Alternatively, it can a an array with the coefficients
            of a polynomy.
        polyfit :


        See also
        --------
        plot_principal_components, plot_principal_components_maps, plot_lev

        """
        # backup the original data
        if on_peaks:
            self._data_before_treatments = self.peak_chars.copy()
        else:
            self._data_before_treatments = self.data.copy()
        # Check for conflicting options and correct them when possible
        if (algorithm == 'mdp' or algorithm == 'NIPALS') and center is False:
            print \
            """
            The PCA algorithms from the MDP toolking (mdp and NIPALS)
            do not permit deactivating data centering.
            Therefore, the algorithm will proceed to center the data.
            """
            center = True
        if algorithm == 'mlpca':
            if normalize_poissonian_noise is True:
                messages.warning(
                "It makes no sense to do normalize_poissonian_noise with "
                "the MLPCA algorithm. Therefore, "
                "normalize_poissonian_noise is set to False")
                normalize_poissonian_noise = False
            if output_dimension is None:
                messages.warning_exit(
                "With the mlpca algorithm the output_dimension must be expecified")

        if center is True and normalize_poissonian_noise is True:
            messages.warning(
            "Centering is not compatible with poissonian noise normalization\n"
            "Disabling centering")
            center = False

        if variance2one is True and normalize_poissonian_noise is True:
            messages.warning(
            "Variance normalization is not compatible with poissonian noise"
            "normalization.\n"
            "Disabling variance2one")
            variance2one = False

        # Apply pre-treatments
        # Centering
        if center is True:
            self.energy_center()
        # Variance normalization
        if variance2one is True:
            self.variance2one()
        # Transform the data in a line spectrum
        self._unfolded4pca = self.unfold_if_multidim()
        # Normalize the poissonian noise
        # Note that this function can change the masks
        if normalize_poissonian_noise is True:
            navigation_mask, signal_mask = \
                self.normalize_poissonian_noise(navigation_mask = navigation_mask,
                                                signal_mask = signal_mask,
                                                return_masks = True)

        navigation_mask = self._correct_navigation_mask_when_unfolded(navigation_mask)

        messages.information('Performing principal components analysis')

        if on_peaks:
            dc=self.peak_chars
        else:
            # The data must be transposed both for Images and Spectra
            dc = self.data.T.squeeze()
        #set the output target (peak results or not?)
        target=self._get_target(on_peaks)
        # Transform the None masks in slices to get the right behaviour
        if navigation_mask is None:
            navigation_mask = slice(None)
        if signal_mask is None:
            signal_mask = slice(None)
        if algorithm == 'mdp' or algorithm == 'NIPALS':
            if algorithm == 'mdp':
                target.pca_node = mdp.nodes.PCANode(
                output_dim=output_dimension, svd = True)
            elif algorithm == 'NIPALS':
                target.pca_node = mdp.nodes.NIPALSNode(
                output_dim=output_dimension)
            # Train the node
            print "\nPerforming the PCA node training"
            print "This include variance normalizing"
            target.pca_node.train(
                dc[signal_mask,:][:,navigation_mask])
            print "Performing PCA projection"
            pc = target.pca_node.execute(dc[:,navigation_mask])
            pca_v = target.pca_node.v
            pca_V = target.pca_node.d
            target.output_dimension = output_dimension

        elif algorithm == 'svd':
            pca_v, pca_V = pca(dc[signal_mask,:][:,navigation_mask])
            pc = np.dot(dc[:,navigation_mask], pca_v)
        elif algorithm == 'fast_svd':
            pca_v, pca_V = pca(dc[signal_mask,:][:,navigation_mask],
            fast = True, output_dimension = output_dimension)
            pc = np.dot(dc[:,navigation_mask], pca_v)

        elif algorithm == 'mlpca' or algorithm == 'fast_mlpca':
            print "Performing the MLPCA training"
            if output_dimension is None:
                messages.warning_exit(
                "For MLPCA it is mandatory to define the output_dimension")
            if var_array is None and var_func is None:
                messages.information('No variance array provided.'
                'Supposing poissonian data')
                var_array = dc.squeeze()[signal_mask,:][:,navigation_mask]

            if var_array is not None and var_func is not None:
                messages.warning_exit(
                "You have defined both the var_func and var_array keywords"
                "Please, define just one of them")
            if var_func is not None:
                if hasattr(var_func, '__call__'):
                    var_array = var_func(dc[signal_mask,...][:,navigation_mask])
                else:
                    try:
                        var_array = np.polyval(polyfit,dc[signal_mask,
                        navigation_mask])
                    except:
                        messages.warning_exit(
                        'var_func must be either a function or an array'
                        'defining the coefficients of a polynom')
            if algorithm == 'mlpca':
                fast = False
            else:
                fast = True
            target.mlpca_output = mlpca(
                dc.squeeze()[signal_mask,:][:,navigation_mask],
                var_array.squeeze(), output_dimension, fast = fast)
            U,S,V,Sobj, ErrFlag  = target.mlpca_output
            print "Performing PCA projection"
            pc = np.dot(dc[:,navigation_mask], V)
            pca_v = V
            pca_V = S ** 2

        if output_dimension:
            print "trimming to %i dimensions"%output_dimension
            pca_v = pca_v[:,:output_dimension]
            pca_V = pca_V[:output_dimension]
            pc = pc[:,:output_dimension]

        target.pc = pc
        target.v = pca_v
        target.V = pca_V
        target.pca_algorithm = algorithm
        target.centered = center
        target.poissonian_noise_normalized = \
            normalize_poissonian_noise
        target.output_dimension = output_dimension
        target.unfolded = self._unfolded4pca
        target.variance2one = variance2one

        if self._unfolded4pca is True:
            target.original_shape = self._shape_before_unfolding

        # Rescale the results if the noise was normalized
        if normalize_poissonian_noise is True:
            target.pc[signal_mask,:] *= self._root_bH
            target.v *= self._root_aG.T
            if isinstance(navigation_mask, slice):
                navigation_mask = None
            if isinstance(signal_mask, slice):
                signal_mask = None

        #undo any pre-treatments
        self.undo_treatments(on_peaks)

        # Set the pixels that were not processed to nan
        if navigation_mask is not None or not isinstance(navigation_mask, slice):
            v = np.zeros((dc.shape[1], target.v.shape[1]),
                    dtype = target.v.dtype)
            v[navigation_mask == False,:] = np.nan
            v[navigation_mask,:] = target.v
            target.v = v

        if self._unfolded4pca is True:
            self.fold()
            self._unfolded4pca is False

    def independent_components_analysis(self, number_of_components = None,
    algorithm = 'CuBICA', diff_order = 1, pc = None,
    comp_list = None, mask = None, on_peaks=False, **kwds):
        """Independent components analysis.

        Available algorithms: FastICA, JADE, CuBICA, and TDSEP

        Parameters
        ----------
        number_of_components : int
            number of principal components to pass to the ICA algorithm
        algorithm : {FastICA, JADE, CuBICA, TDSEP}
        diff : bool
        diff_order : int
        pc : numpy array
            externally provided components
        comp_list : boolen numpy array
            choose the components to use by the boolen list. It permits to
            choose non contiguous components.
        mask : numpy boolean array with the same dimension as the PC
            If not None, only the selected channels will be used by the
            algorithm.
        """
        target=self._get_target(on_peaks)

        if hasattr(target, 'pc'):
            if pc is None:
                pc = target.pc
            bool_index = np.zeros((pc.shape[0]), dtype = 'bool')
            if number_of_components is not None:
                bool_index[:number_of_components] = True
            else:
                if self.output_dimension is not None:
                    number_of_components = self.output_dimension
                    bool_index[:number_of_components] = True

            if comp_list is not None:
                for ipc in comp_list:
                    bool_index[ipc] = True
                number_of_components = len(comp_list)
            pc = pc[:,bool_index]
            if diff_order > 0:
                pc = np.diff(pc, diff_order, axis = 0)
            if mask is not None:
                pc = pc[mask, :]

            else:
                # first centers and scales data
                invsqcovmat, pc = center_and_scale(pc).itervalues()
                exec('target.ica_node=mdp.nodes.%sNode(white_parm = \
                {\'svd\' : True})' % algorithm)
                target.ica_node.variance2oneed = True
                target.ica_node.train(pc)
                target.w = np.dot(target.ica_node.get_recmatrix(), invsqcovmat)
            self._ic_from_w(target)
            target.ica_scores=self._get_ica_scores(target)
            target.ica_algorithm = algorithm
            self.output_dimension = number_of_components
        else:
            "You have to perform Principal Components Analysis before"
            sys.exit(0)

    def reverse_ic(self, ic_n, on_peaks = False):
        """Reverse the independent component

        Parameters
        ----------
        ic_n : list or int
            component index/es

        Examples
        -------
        >>> s = load('some_file')
        >>> s.principal_components_analysis(True) # perform PCA
        >>> s.independent_components_analysis(3)  # perform ICA on 3 PCs
        >>> s.reverse_ic(1) # reverse IC 1
        >>> s.reverse_ic((0, 2)) # reverse ICs 0 and 2
        """
        if on_peaks:
            target=self.peak_mva_results
        else:
            target=self.mva_results

        for i in [ic_n,]:
            target.ic[:,i] *= -1
            target.w[i,:] *= -1

    def _ic_from_w(self,target):
        w = target.w
        n = len(w)
        target.ic = np.dot(target.pc[:,:n], w.T)
        for i in xrange(n):
            if np.all(target.ic[:,i] <= 0):
                self.reverse_ic(i)

    def _get_ica_scores(self,target):
        """
        Returns the ICA score matrix (formerly known as the recmatrix)
        """
        W = target.v.T[:target.ic.shape[1],:]
        Q = np.linalg.inv(target.w.T)
        return np.dot(Q,W)

    def _calculate_recmatrix(self, components = None, mva_type=None,
                             on_peaks=False):
        """
        Rebuilds SIs from selected components

        Parameters
        ------------
        target : target or self.peak_mva_results
        components : None, int, or list of ints
             if None, rebuilds SI from all components
             if int, rebuilds SI from components in range 0-given int
             if list of ints, rebuilds SI from only components in given list
        mva_type : string, currently either 'pca' or 'ica'
             (not case sensitive)

        Returns
        -------
        Signal instance
        """

        target=self._get_target(on_peaks)

        if mva_type.lower() == 'pca':
            factors = target.pc
            scores = target.v.T
        elif mva_type.lower() == 'ica':
            factors = target.ic
            scores = self._get_ica_scores(target)
        if components is None:
            a = np.atleast_3d(np.dot(factors,scores))
            signal_name = 'rebuilt from %s with %i components' % (
            mva_type,factors.shape[1])
        elif hasattr(components, '__iter__'):
            tfactors = np.zeros((factors.shape[0],len(components)))
            tscores = np.zeros((len(components),scores.shape[1]))
            for i in xrange(len(components)):
                tfactors[:,i] = factors[:,components[i]]
                tscores[i,:] = scores[components[i],:]
            a = np.atleast_3d(np.dot(tfactors, tscores))
            signal_name = 'rebuilt from %s with components %s' % (
            mva_type,components)
        else:
            a = np.atleast_3d(np.dot(factors[:,:components],
                                     scores[:components,:]))
            signal_name = 'rebuilt from %s with %i components' % (
            mva_type,components)

        self._unfolded4pca = self.unfold_if_multidim()

        sc = self.deepcopy()

        import hyperspy.signals.spectrum
        if isinstance(self, hyperspy.signals.spectrum.Spectrum):
            print "Transposing data so that energy axis makes up rows."
            sc.data = a.T.squeeze()
        else:
            sc.data = a.squeeze()
        sc.name = signal_name
        if self._unfolded4pca is True:
            self.fold()
            sc.history = ['unfolded']
            sc.fold()
        return sc

    def pca_build_SI(self, components=None, on_peaks=False):
        """Return the spectrum generated with the selected number of principal
        components

        Parameters
        ------------
        components : None, int, or list of ints
             if None, rebuilds SI from all components
             if int, rebuilds SI from components in range 0-given int
             if list of ints, rebuilds SI from only components in given list

        Returns
        -------
        Signal instance
        """
        rec=self._calculate_recmatrix(components=components, mva_type='pca',
                                         on_peaks=on_peaks)
        rec.residual=rec.copy()
        rec.residual.data=self.data-rec.data
        return rec

    def ica_build_SI(self,components = None, on_peaks=False):
        """Return the spectrum generated with the selected number of
        independent components

        Parameters
        ------------
        components : None, int, or list of ints
             if None, rebuilds SI from all components
             if int, rebuilds SI from components in range 0-given int
             if list of ints, rebuilds SI from only components in given list

        Returns
        -------
        Signal instance
        """
        return self._calculate_recmatrix(components=components, mva_type='ica',
                                         on_peaks=on_peaks)

    def energy_center(self):
        """Subtract the mean energy pixel by pixel"""
        print "\nCentering the energy axis"
        self._energy_mean = np.mean(self.data, 0)
        self.data = (self.data - self._energy_mean)
        self._replot()

    def undo_energy_center(self):
        if hasattr(self,'_energy_mean'):
            self.data = (self.data + self._energy_mean)
            self._replot()

    def variance2one(self, on_peaks=False):
        # Whitening
        if on_peaks:
            d=self.peak_chars
        else:
            d=self.data
        self._std = np.std(d, 0)
        d /= self._std
        self._replot()

    def undo_variance2one(self, on_peaks=False):
        if on_peaks:
            d=self.peak_chars
        else:
            d=self.data
        if hasattr(self,'_std'):
            d *= self._std
            self._replot()

    def plot_lev(self, n=50, on_peaks=False):
        """Plot the principal components LEV up to the given number

        Parameters
        ----------
        n : int
        """
        target = self._get_target(on_peaks)
        if n>target.V.shape[0]:
            n=target.V.shape[0]
        fig = plt.figure()
        ax = fig.add_subplot(111)
        ax.plot(range(n), target.V[:n], 'o')
        ax.semilogy()
        ax.set_title('Log(eigenvalues)')
        ax.set_xlabel('Principal component')
        plt.draw()
        plt.show()
        return ax

    def plot_explained_variance(self,n=50,on_peaks=False):
        """Plot the principal components explained variance up to the given
        number

        Parameters
        ----------
        n : int
        """
        target = self._get_target(on_peaks)
        if n > target.V.shape[0]:
            n=target.V.shape[0]
        cumu = np.cumsum(target.V) / np.sum(target.V)
        fig = plt.figure()
        ax = fig.add_subplot(111)
        ax.scatter(range(n), cumu[:n])
        ax.set_xlabel('Principal component')
        ax.set_ylabel('Explained variance')
        plt.draw()
        plt.show()
        return ax

    def plot_principal_components(self, n = None, on_peaks=False):
        """Plot the principal components up to the given number

        Parameters
        ----------
        n : int
            number of principal components to plot.
        """
        target=self._get_target(on_peaks)
        if n is None:
            n = target.pc.shape[1]
        for i in xrange(n):
            plt.figure()
            plt.plot(self.axes_manager.axes[-1].axis, target.pc[:,i])
            plt.title('Principal component %s' % i)
            plt.xlabel('Energy (eV)')

    def plot_independent_components(self, ic=None, same_window=False,
                                    on_peaks=False):
        """Plot the independent components.

        Parameters
        ----------
        ic : numpy array (optional)
             externally provided independent components array
             The shape of 'ic' must be (channels, n_components),
             so that e.g. ic[:, 0] is the first independent component.

        same_window : bool (optional)
                    if 'True', the components will be plotted in the
                    same window. Default is 'False'.
        """
        target=self._get_target(on_peaks)
        if ic is None:
            ic = target.ic
            x = self.axes_manager.axes[-1].axis
            x = ic.shape[1]     # no way that we know the calibration

        n = ic.shape[1]

        if not same_window:
            for i in xrange(n):
                plt.figure()
                plt.plot(x, ic[:, i])
                plt.title('Independent component %s' % i)
                plt.xlabel('Energy (eV)')
        else:
            fig = plt.figure()
            ax = fig.add_subplot(111)
            for i in xrange(n):
                # ic = ic / ic.sum(axis=0) # normalize
                lbl = 'IC %i' % i
                # print 'plotting %s' % lbl
                ax.plot(x, ic[:, i], label=lbl)
            col = (ic.shape[1]) // 2
            ax.legend(ncol=col, loc='best')
            ax.set_xlabel('Energy (eV)')
            ax.set_title('Independent components')
            plt.draw()
            plt.show()

    def plot_maps(self, components, mva_type=None, scores=None, factors=None,
                  cmap=plt.cm.gray, no_nans=False, with_components=True,
                  plot=True, on_peaks=False, directory = None):
        """
        Plot component maps for the different MSA types

        Parameters
        ----------
        components : None, int, or list of ints
            if None, returns maps of all components.
            if int, returns maps of components with ids from 0 to given int.
            if list of ints, returns maps of components with ids in given list.
        mva_type: string, currently either 'pca' or 'ica'
        scores: numpy array, the array of score maps
        factors: numpy array, the array of components, with each column as a component.
        cmap: matplotlib colormap instance
        no_nans: bool,
        with_components: bool,
        plot: bool,
        """
        from hyperspy.signals.image import Image
        from hyperspy.signals.spectrum import Spectrum

        target=self._get_target(on_peaks)

        if scores is None or (factors is None and with_components is True):
            print "Either recmatrix or components were not provided."
            print "Loading existing values from object."
            if mva_type is None:
                print "Neither scores nor analysis type specified.  Cannot proceed."
                return

            elif mva_type.lower() == 'pca':
                scores=target.v.T
                factors=target.pc
            elif mva_type.lower() == 'ica':
                scores = self._get_ica_scores(target)
                factors=target.ic
                if no_nans:
                    print 'Removing NaNs for a visually prettier plot.'
                    scores = np.nan_to_num(scores) # remove ugly NaN pixels
            else:
                print "No scores provided and analysis type '%s' unrecognized. Cannot proceed."%mva_type
                return

#        if len(self.axes_manager.axes)==2:
#            shape=self.data.shape[0],1
#        else:
#            shape=self.data.shape[0],self.data.shape[1]
        im_list = []

        if components is None:
            components=xrange(factors.shape[1])

        elif type(components).__name__!='list':
            components=xrange(components)

        for i in components:
            if plot is True:
                figure = plt.figure()
                if with_components:
                    ax = figure.add_subplot(121)
                    ax2 = figure.add_subplot(122)
                else:
                    ax = figure.add_subplot(111)
            if self.axes_manager.navigation_dimension == 2:
                toplot = scores[i,:].reshape(self.axes_manager.navigation_shape)
                im_list.append(Image({'data' : toplot,
                    'axes' : self.axes_manager._get_non_slicing_axes_dicts()}))
                if plot is True:
                    mapa = ax.matshow(toplot, cmap = cmap)
                    if with_components:
                        ax2.plot(self.axes_manager.axes[-1].axis, factors[:,i])
                        ax2.set_title('%s component %i' % (mva_type.upper(),i))
                        ax2.set_xlabel('Energy (eV)')
                    figure.colorbar(mapa)
                    figure.canvas.draw()
                    #pointer = widgets.DraggableSquare(self.coordinates)
                    #pointer.add_axes(ax)
            elif self.axes_manager.navigation_dimension == 1:
                toplot = scores[i,:]
                im_list.append(Spectrum({"data" : toplot,
                    'axes' : self.axes_manager._get_non_slicing_axes_dicts()}))
                im_list[-1].get_dimensions_from_data()
                if plot is True:
                    ax.step(range(len(toplot)), toplot)

                    if with_components:
                        ax2.plot(self.axes_manager.axes[-1].axis, factors[:,i])
                        ax2.set_title('%s component %s' % (mva_type.upper(),i))
                        ax2.set_xlabel('Energy (eV)')
            else:
                messages.warning_exit('View not supported')
            if plot is True:
                ax.set_title('%s component number %s map' % (mva_type.upper(),i))
                figure.canvas.draw()
                if directory is not None:
                    if not os.path.isdir(directory):
                        os.makedirs(directory)
                    figure.savefig(os.path.join(directory, 'IC-%i.png' % i),
                              dpi = 600)
        return im_list

    def plot_principal_components_maps(self, comp_ids=None, cmap=plt.cm.gray,
                                       recmatrix=None, with_pc=True,
                                       plot=True, pc=None, on_peaks=False):
        """Plot the map associated to each independent component

        Parameters
        ----------
        comp_ids : None, int, or list of ints
            if None, returns maps of all components.
            if int, returns maps of components with ids from 0 to given int.
            if list of ints, returns maps of components with ids in given list.
        cmap : plt.cm object
        recmatrix : numpy array
            externally suplied recmatrix
        with_ic : bool
            If True, plots also the corresponding independent component in the
            same figure
        plot : bool
            If True it will plot the figures. Otherwise it will only return the
            images.
        ic : numpy array
            externally supplied independent components
        no_nans : bool (optional)
             whether substituting NaNs with zeros for a visually prettier plot
             (default is False)

        Returns
        -------
        List with the maps as MVA instances
        """
        return self.plot_maps(components=comp_ids,mva_type='pca',cmap=cmap,
                              scores=recmatrix, with_components=with_pc,
                              plot=plot, factors=pc, on_peaks=on_peaks)

    def plot_independent_components_maps(self, comp_ids=None, cmap=plt.cm.gray,
                                         recmatrix=None, with_ic=True,
                                         plot=True, ic=None, no_nans=False,
                                         on_peaks=False, directory = None):
        """Plot the map associated to each independent component

        Parameters
        ----------
        cmap : plt.cm object
        recmatrix : numpy array
            externally suplied recmatrix
        comp_ids : int or list of ints
            if None, returns maps of all components.
            if int, returns maps of components with ids from 0 to given int.
            if list of ints, returns maps of components with ids in given list.
        with_ic : bool
            If True, plots also the corresponding independent component in the
            same figure
        plot : bool
            If True it will plot the figures. Otherwise it will only return the
            images.
        ic : numpy array
            externally supplied independent components
        no_nans : bool (optional)
             whether substituting NaNs with zeros for a visually prettier plot
             (default is False)
        Returns
        -------
        List with the maps as MVA instances
        """
        return self.plot_maps(components=comp_ids,mva_type='ica',cmap=cmap,
                              scores=recmatrix, with_components=with_ic,
                              plot=plot, factors=ic, no_nans=no_nans,
                              on_peaks=on_peaks, directory = directory)


    def save_principal_components(self, n, spectrum_prefix = 'pc',
    image_prefix = 'im', spectrum_format = 'msa', image_format = 'tif',
                                  on_peaks=False):
        """Save the `n` first principal components  and score maps
        in the specified format

        Parameters
        ----------
        n : int
            Number of principal components to save_als_ica_results
        image_prefix : string
            Prefix for the image file names
        spectrum_prefix : string
            Prefix for the spectrum file names
        spectrum_format : string
        image_format : string

        """
        from spectrum import Spectrum
        target=self._get_target(on_peaks)
        im_list = self.plot_principal_components_maps(n, plot = False,
                                                      on_peaks=on_peaks)
        s = Spectrum({'calibration' : {'data_cube' : target.pc[:,0]}})
        s.get_calibration_from(self)
        for i in xrange(n):
            s.data_cube = target.pc[:,i]
            s.get_dimensions_from_cube()
            s.save('%s-%i.%s' % (spectrum_prefix, i, spectrum_format))
            im_list[i].save('%s-%i.%s' % (image_prefix, i, image_format))

    def save_independent_components(self, elements=None,
                                    spectrum_format='msa',
                                    image_format='tif',
                                    recmatrix=None, ic=None,
                                    on_peaks=False):
        """Saves the result of the ICA in image and spectrum format.
        Note that to save the image, the NaNs in the map will be converted
        to zeros.

        Parameters
        ----------
        elements : None or tuple of strings
            a list of names (normally an element) to be assigned to IC. If not
            the will be name ic-0, ic-1 ...
        image_format : string
        spectrum_format : string
        recmatrix : None or numpy array
            externally supplied recmatrix
        ic : None or numpy array
            externally supplied IC
        """
        from hyperspy.signals.spectrum import Spectrum
        target=self._get_target(on_peaks)
        pl = self.plot_independent_components_maps(plot=False,
                                                   recmatrix=recmatrix,
                                                   ic=ic,
                                                   no_nans=True,
                                                   on_peaks=on_peaks)
        if ic is None:
            ic = target.ic
        if self.data.shape[2] > 1:
            maps = True
        else:
            maps = False
        for i in xrange(ic.shape[1]):
            axes = (self.axes_manager._slicing_axes[0].get_axis_dictionary(),)
            axes[0]['index_in_array'] = 0
            spectrum = Spectrum({'data' : ic[:,i], 'axes' : axes})
            spectrum.data_cube = ic[:,i].reshape((-1,1,1))

            if elements is None:
                spectrum.save('ic-%s.%s' % (i, spectrum_format))
                if maps is True:
                    pl[i].save('map_ic-%s.%s' % (i, image_format))
                else:
                    pl[i].save('profile_ic-%s.%s' % (i, spectrum_format))
            else:
                element = elements[i]
                spectrum.save('ic-%s.%s' % (element, spectrum_format))
                if maps:
                    pl[i].save('map_ic-%s.%s' % (element, image_format))
                else:
                    pl[i].save('profile_ic-%s.%s' % (element, spectrum_format))

    def als(self, **kwargs):
        """Alternate Least Squares imposing positivity constraints
        to the result of a previous ICA

        Stores in result in self.als_out

        Parameters
        ----------
        thresh : float
            default=.001
        nonnegS : bool
            Impose non-negative constraint of the components. Default: True
        nonnegC : bool
            Impose non-negative constraint of the maps. Default: True

        See also
        -------
        plot_als_ic_maps, plot_als_ic
        """
        shape = (self.data.shape[2], self.data.shape[1],-1)
        if hasattr(self, 'ic') and (self.ic is not None):
            also = utils.ALS(self, **kwargs)
            self.als_ic = also['S']
            self.als_maps = also['CList'].reshape(shape, order = 'C')
            self.als_output = also

    def plot_als_ic_maps(self):
        """Same as plot_ic_maps for the ALS results"""
        return self.plot_independent_components_maps(recmatrix =
        self.als_output['CList'].T, ic = self.als_ic)

    def plot_als_ic(self):
        """Same as plot_independent_componets for the ALS results"""
        self.plot_independent_components(ic = self.als_ic)

    def save_als_ica_results(self, elements = None,
    format = defaults.file_format, image_format = 'tif'):
        """Same as save_ica_results for the ALS results"""
        self.save_ica_results(elements = elements, image_format = image_format,
        recmatrix = self.als_output['CList'].T, ic = self.als_ic)

    def normalize_poissonian_noise(self, navigation_mask = None,
                                   signal_mask = None, return_masks = False):
        """
        Scales the SI following Surf. Interface Anal. 2004; 36: 203–212 to
        "normalize" the poissonian data for PCA analysis

        Parameters
        ----------
        navigation_mask : boolen numpy array
        signal_mask  : boolen numpy array
        """
        messages.information(
            "Scaling the data to normalize the (presumably) Poissonian noise")
        # If energy axis is not first, it needs to be for MVA.
        refold = self.unfold_if_multidim()
        dc = self.data.T.squeeze().copy()
        navigation_mask = \
            self._correct_navigation_mask_when_unfolded(navigation_mask)
        if navigation_mask is None:
            navigation_mask = slice(None)
        if signal_mask is None:
            signal_mask = slice(None)
        # Rescale the data to gaussianize the poissonian noise
        aG = dc[signal_mask,:][:,navigation_mask].sum(0).squeeze()
        bH = dc[signal_mask,:][:,navigation_mask].sum(1).squeeze()
        # Checks if any is negative
        if (aG < 0).any() or (bH < 0).any():
            messages.warning_exit(
            "Data error: negative values\n"
            "Are you sure that the data follow a poissonian distribution?")
        # Update the spatial and energy masks so it does not include rows
        # or colums that sum zero.
        aG0 = (aG == 0)
        bH0 = (bH == 0)
        if aG0.any():
            if isinstance(navigation_mask, slice):
                # Convert the slice into a mask before setting its values
                navigation_mask = np.ones((self.data.shape[1]),dtype = 'bool')
            # Set colums summing zero as masked
            navigation_mask[aG0] = False
            aG = aG[aG0 == False]
        if bH0.any():
            if isinstance(signal_mask, slice):
                # Convert the slice into a mask before setting its values
                signal_mask = np.ones((self.data.shape[0]), dtype = 'bool')
            # Set rows summing zero as masked
            signal_mask[bH0] = False
            bH = bH[bH0 == False]
        self._root_aG = np.sqrt(aG)[np.newaxis,:]
        self._root_bH = np.sqrt(bH)[:, np.newaxis]
        temp = (dc[signal_mask,:][:,navigation_mask] /
                (self._root_aG * self._root_bH))
        if  isinstance(signal_mask,slice) or isinstance(navigation_mask,slice):
            dc[signal_mask,navigation_mask] = temp
        else:
            mask3D = signal_mask[:, np.newaxis] * \
                navigation_mask[np.newaxis, :]
            dc[mask3D] = temp.ravel()
        # TODO - dc was never modifying self.data - was normalization ever
        # really getting applied?  Comment next lines as necessary.
        self.data = dc.T.copy()
        # end normalization write to self.data.
        if refold is True:
            print "Automatically refolding the SI after scaling"
            self.fold()
        if return_masks is True:
            if isinstance(navigation_mask, slice):
                navigation_mask = None
            if isinstance(signal_mask, slice):
                signal_mask = None
            return navigation_mask, signal_mask

    def undo_treatments(self, on_peaks=False):
        """Undo normalize_poissonian_noise"""
        print "Undoing data pre-treatments"
        if on_peaks:
            self.peak_chars=self._data_before_treatments
            del self._data_before_treatments
        else:
            self.data=self._data_before_treatments
            del self._data_before_treatments

    def peak_pca(self):
        self.principal_components_analysis(on_peaks=True)

    def peak_ica(self, number_of_components):
        self.independent_components_analysis(number_of_components, on_peaks=True)

class MVA_Results():
    def __init__(self):
        self.pc = None
        self.v = None
        self.V = None
        self.pca_algorithm = None
        self.ica_algorithm = None
        self.centered = None
        self.poissonian_noise_normalized = None
        self.output_dimension = None
        self.unfolded = None
        self.original_shape = None
        self.ica_node=None
        # Demixing matrix
        self.w = None


    def save(self, filename):
        """Save the result of the PCA analysis

        Parameters
        ----------
        filename : string
        """
        np.savez(filename, pc = self.pc, v = self.v, V = self.V,
        pca_algorithm = self.pca_algorithm, centered = self.centered,
        output_dimension = self.output_dimension, variance2one = self.variance2one,
        poissonian_noise_normalized = self.poissonian_noise_normalized,
        w = self.w, ica_algorithm = self.ica_algorithm)

    def load(self, filename):
        """Load the result of the PCA analysis

        Parameters
        ----------
        filename : string
        """
        pca = np.load(filename)
        for key in pca.files:
            exec('self.%s = pca[\'%s\']' % (key, key))
        print "\n%s loaded correctly" %  filename

        # For compatibility with old version
        if hasattr(self, 'algorithm'):
            self.pca_algorithm = self.algorithm
            del self.algorithm
        defaults = {
        'centered' : False,
        'variance2one' : False,
        'poissonian_noise_normalized' : False,
        'output_dimension' : None,
        'last_used_pca_algorithm' : None
        }
        for attrib in defaults.keys():
            if not hasattr(self, attrib):
                exec('self.%s = %s' % (attrib, defaults[attrib]))
        self.summary()

    def summary(self):
        """Prints a summary of the PCA parameters to the stdout
        """
        print
        print "MVA Summary:"
        print "------------"
        print
        print "PCA algorithm : ", self.pca_algorithm
        print "Scaled to normalize poissonina noise : %s" % \
        self.poissonian_noise_normalized
        print "Energy centered : %s" % self.centered
        print "Variance normalized : %s" % self.variance2one
        print "Output dimension : %s" % self.output_dimension
        print "ICA algorithm : %s" % self.ica_algorithm

    def crop_v(self, n):
        """
        Crop the score matrix up to the given number.

        It is mainly useful to save memory and redude the storage size
        """
        self.v = self.v[:,:n].copy()
