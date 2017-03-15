# coding: utf-8
# Copyright (c) Materials Virtual Lab.
# Distributed under the terms of the BSD License.

from __future__ import division, unicode_literals, print_function

from pymatgen.core import Structure, PeriodicSite
import warnings
import numpy as np

__author__ = "Iek-Heng Chu"
__version__ = "1.0"
__date__ = "March 14, 2017"

"""
Algorithms for improving initial NEB migration path.
"""


# TODO: (1) ipython notebook example files, unittests


class IDPPSolver(object):
    """
    A solver using image dependent pair potential (IDPP) algo to get an improved
    initial NEB path. For more details about this algo, please refer to Smidstrup
    et al., J. Chem. Phys. 140, 214106 (2014).

    """

    def __init__(self, structures, spring_const=5.0):
        """
        Initialization.

        Args:
        structures (list of pmg_structure) : Initial guess of the NEB path (including
                    initial and final end-point structures)
        spring_const (float): A virtual spring constant used in the NEB-like relaxation
                            process that yields so-called IDPP path.
        """

        lattice = structures[0].lattice
        natoms = structures[0].num_sites
        nimages = len(structures) - 2
        target_dists = []

        # Initial guess of the path used in the IDPP algo.
        init_cart_coords = []

        # Construct the set of target distance matrices via linear interpolation between
        # those of end-point structures.
        for i in range(1, nimages + 1):
            # Interpolated distance matrices
            dist = structures[0].distance_matrix + (structures[-1].distance_matrix -
                                                    structures[0].distance_matrix) * i \
                                                   / (nimages + 1)

            target_dists.append(dist)

        target_dists = np.array(target_dists)

        # Set of translational vector matrices (anti-symmetric) for the images.
        translations = np.zeros((nimages, natoms, natoms, 3), dtype=np.float64)

        # A set of weight functions. It is set as 1/d^4 for each image. Here, we take d as
        # the average of the target distance matrix and the actual distance matrix.
        weights = np.zeros_like(target_dists, dtype=np.float64)
        for ni in range(nimages):
            avg_dist = (target_dists[ni] + structures[ni + 1].distance_matrix) / 2.0
            weights[ni] = 1.0 / (avg_dist ** 4 +
                                np.eye(natoms, dtype=np.float64) * 1e-8)

        for ni in range(nimages + 2):
            for i in range(natoms):
                frac_coords = structures[ni][i].frac_coords
                init_cart_coords.append(lattice.get_cartesian_coords(frac_coords))

                if ni not in [0, nimages + 1]:
                    for j in range(i + 1, natoms):
                        img = lattice.get_distance_and_image(frac_coords,
                                                             structures[ni][j].frac_coords)[1]
                        translations[ni - 1, i, j] = lattice.get_cartesian_coords(img)
                        translations[ni - 1, j, i] = -lattice.get_cartesian_coords(img)

        self.init_coords = np.array(init_cart_coords).reshape(nimages + 2, natoms, 3)
        self.translations = translations
        self.weights = weights
        self.structures = structures
        self.target_dists = target_dists
        self.spring_const = spring_const
        self.nimages = nimages


    def run(self, maxiter=1000, tol=1e-5, gtol=1e-3, step_size=0.05, max_disp=0.05,
            species=None):

        """
        Perform iterative minimization of the set of objective functions in an NEB-like
        manner. In each iteration, the total force matrix for each image is constructed,
        which comprises both the spring forces and true forces. For more details about the
        NEB approach, please see the references, e.g. Henkelman et al., J. Chem. Phys.
        113, 9901 (2000).

        Args:
            maxiter (int): Maximum number of iterations in the minimization process.
            tol (float): Tolerance of the change of objective functions between
                        consecutive steps.
            gtol (float): Tolerance of maximum force component (absolute value).
            step_size (float): Step size associated with the displacement of the atoms
                        during the minimization process.
            max_disp (float): Maximum allowed atomic displacement in each iteration.
            species (list of string): If provided, only those given species are allowed
                                to move. The atomic positions of other species are
                                obtained via regular linear interpolation approach.

        At the end, the complete IDPP path (including end-point structures) will be
        returned.
        """

        iter = 0
        coords = self.init_coords.copy()
        old_funcs = np.zeros((self.nimages,), dtype=np.float64)
        idpp_structures = [self.structures[0]]

        if species is None:
            indices = [i for i, site in enumerate(self.structures[0])]
        else:
            indices = [i for i, site in enumerate(self.structures[0])
                       if site.specie.symbol in species]

            if len(indices) == 0:
                raise ValueError("Error! The given species are not in the system!")

        # Iterative minimization
        while (iter <= maxiter):
            # Get the sets of objective functions, true and total force matrices.
            funcs, true_forces = self._get_funcs_and_forces(coords)
            tot_forces = self._get_total_forces(coords, true_forces)

            # Each atom is allowed to move up to max_disp
            for ni in range(self.nimages):
                disp_mat = step_size * tot_forces[ni, indices, :]
                for i in indices:
                    for j in range(3):
                        if abs(disp_mat[i, j]) > max_disp:
                            disp_mat[i, j] /= (abs(disp_mat[i, j]) / max_disp)

                coords[ni + 1, indices] += disp_mat

            max_force = np.abs(tot_forces[:, indices, :]).max()
            tot_res = np.sum(np.abs(old_funcs - funcs))

            if tot_res < tol and max_force < gtol:
                break
            else:
                old_funcs = funcs

            if iter == maxiter:
                warnings.warn("Maximum iteration number is reached, not converged yet!",
                              UserWarning)
                break

            iter += 1

        for ni in range(self.nimages):
            # generate the improved image structure
            new_sites = []

            for site, cart_coords in zip(self.structures[ni + 1], coords[ni + 1]):
                new_site = PeriodicSite(site.species_and_occu, coords=cart_coords,
                                        lattice=site.lattice, coords_are_cartesian=True,
                                        properties=site.properties)
                new_sites.append(new_site)

            idpp_structures.append(Structure.from_sites(new_sites))

        # Also include end-point structure.
        idpp_structures.append(self.structures[-1])

        return idpp_structures


    @classmethod
    def from_endpoints(cls, endpoints, nimages=5, sort_tol=1.0, spring_const=5.0):
        """
        A class method that starts with end-point structures instead. The initial
        guess for the IDPP algo is then constructed using the regular linear
        interpolation approach.

        Args:
            endpoints (list of Structure objects): The two end-point structures.
            nimages (int): Number of images between the two end-points.
            sort_tol (float): Distance tolerance (in Angstrom) used to match the
                            atomic indices between start and end structures. Need
                            to increase the value in some cases.
            spring_const (float): A virtual spring constant used in the NEB-like
                            relaxation process that yields so-called IDPP path.

        """

        try:
            images = endpoints[0].interpolate(endpoints[1], nimages=nimages + 1,
                                             autosort_tol=sort_tol)
        except Exception as e:
            if "Unable to reliably match structures " in str(e):
                warnings.warn("Auto sorting is turned off because it is unable to "
                              "match the end-point structures!", UserWarning)
                images = endpoints[0].interpolate(endpoints[1],
                                                  nimages=nimages + 1,
                                                  autosort_tol=0)
            else:
                raise e

        return IDPPSolver(images, spring_const=spring_const)


    def _get_funcs_and_forces(self, x):
        """
        Calculate the set of objective functions as well as their gradients, i.e.
        "effective true forces"
        """
        funcs = []
        funcs_prime = []
        natoms = np.shape(self.translations)[1]

        for ni in range(len(x) - 2):
            vec = np.array([x[ni + 1, i] - x[ni + 1] -
                            self.translations[ni, i] for i in range(natoms)])
            trial_dist = np.linalg.norm(vec, axis=2)
            aux = -2.0 * (trial_dist - self.target_dists[ni]) * self.weights[ni] \
                  / (trial_dist + np.eye(natoms, dtype=np.float64))

            # Objective function
            func = 0.5 * np.sum((trial_dist -
                                 self.target_dists[ni]) ** 2 * self.weights[ni])

            # "True force" derived from the objective function.
            grad = np.zeros_like(x[0], dtype=np.float64)

            for i in range(natoms):
                grad[i] = np.dot(aux[i], vec[i])

            funcs.append(func)
            funcs_prime.append(grad)

        return np.array(funcs), np.array(funcs_prime)


    @staticmethod
    def get_unit_vector(vec):
        return vec / np.linalg.norm(vec)


    def _get_total_forces(self, x, true_forces):
        """
        Calculate the total force on each image structure, which is equal to the spring
        force along the tangent + true force perpendicular to the tangent. Note that the
        spring force is the modified version in the literature (e.g. Henkelman et al.,
        J. Chem. Phys. 113, 9901 (2000)).
        """

        total_forces = []
        natoms = np.shape(true_forces)[1]

        for ni in range(1, len(x) - 1):
            vec1 = (x[ni + 1] - x[ni]).flatten()
            vec2 = (x[ni] - x[ni - 1]).flatten()

            # Local tangent
            tangent = self.get_unit_vector(vec1) + self.get_unit_vector(vec2)
            tangent = self.get_unit_vector(tangent)

            # Spring force
            spring_force = self.spring_const * (np.linalg.norm(vec1) -
                                                np.linalg.norm(vec2)) * tangent

            # Total force
            flat_ft = true_forces[ni - 1].copy().flatten()
            total_force = true_forces[ni - 1] + (spring_force - np.dot(flat_ft, tangent) *
                                                 tangent).reshape(natoms, 3)
            total_forces.append(total_force)

        return np.array(total_forces)