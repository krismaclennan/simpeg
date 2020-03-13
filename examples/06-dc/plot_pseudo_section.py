"""
DC Forward Simulation
=====================

Forward model two conductive spheres in a half-space and plot a
pseudo-section. Assumes an infinite line source and measures along the
center of the spheres.

INPUT:
loc     = Location of spheres [[x1, y1, z1], [x2, y2, z2]]
radi    = Radius of spheres [r1, r2]
param   = Conductivity of background and two spheres [m0, m1, m2]
survey_type = survey type 'pole-dipole' or 'dipole-dipole'
unitType = Data type "appResistivity" | "appConductivity"  | "volt"
Created by @fourndo

"""
import time
import numpy as np
import scipy.sparse as sp
import matplotlib.pyplot as plt

import discretize
from SimPEG import utils
from SimPEG.data import Data
from SimPEG.electromagnetics.static.utils import gen_DCIPsurvey
from SimPEG.electromagnetics.static.utils import convertObs_DC3D_to_2D
from SimPEG.electromagnetics.static.utils import plot_pseudoSection


def run(loc=None, sig=None, radi=None, param=None, survey_type='dipole-dipole',
        unitType='appConductivity', plotIt=True):

    assert survey_type in [
        'pole-dipole', 'dipole-dipole', 'pole-dipole', 'pole-pole'
        ], (
            """survey_type must be 'dipole-dipole' | 'pole-dipole' |
            'dipole-pole' | 'pole-pole'"""
            " not {}".format(survey_type)
    )

    assert unitType in ['appResistivity', 'appConductivity', 'volt'], (
        "Unit type (unitType) must be appResistivity or "
        "appConductivity or volt (potential)"
    )

    if loc is None:
        loc = np.c_[[-50., 0., -50.], [50., 0., -50.]]
    if sig is None:
        sig = np.r_[1e-2, 1e-1, 1e-3]
    if radi is None:
        radi = np.r_[25., 25.]
    if param is None:
        param = np.r_[30., 30., 5]

    dx = 5.

    hxind = [(dx, 15, -1.3), (dx, 75), (dx, 15, 1.3)]
    hyind = [(dx, 15, -1.3), (dx, 10), (dx, 15, 1.3)]
    hzind = [(dx, 15, -1.3), (dx, 15)]

    mesh = discretize.TensorMesh([hxind, hyind, hzind], 'CCN')

    # Set background conductivity
    model = np.ones(mesh.nC) * sig[0]

    # First anomaly
    ind = utils.model_builder.getIndicesSphere(loc[:, 0], radi[0], mesh.gridCC)
    model[ind] = sig[1]

    # Second anomaly
    ind = utils.model_builder.getIndicesSphere(loc[:, 1], radi[1], mesh.gridCC)
    model[ind] = sig[2]

    # Get index of the center
    indy = int(mesh.nCy/2)

    # Plot the model for reference
    # Define core mesh extent
    xlim = 200
    zlim = 100

    # Then specify the end points of the survey. Let's keep it simple for now
    # and survey above the anomalies, top of the mesh
    ends = [(-175, 0), (175, 0)]
    ends = np.c_[np.asarray(ends), np.ones(2).T*mesh.vectorNz[-1]]

    # Snap the endpoints to the grid. Easier to create 2D section.
    indx = utils.closestPoints(mesh, ends)
    locs = np.c_[
        mesh.gridCC[indx, 0],
        mesh.gridCC[indx, 1],
        np.ones(2).T*mesh.vectorNz[-1]
    ]

    # We will handle the geometry of the survey for you and create all the
    # combination of tx-rx along line
    survey = gen_DCIPsurvey(
        locs, dim=mesh.dim, survey_type=survey_type,
        a=param[0], b=param[1], n=param[2]
    )
    Tx = survey.source_list
    Rx = [src.receiver_list[0] for src in Tx]
    # Define some global geometry
    dl_len = np.sqrt(np.sum((locs[0, :] - locs[1, :])**2))
    dl_x = (Tx[-1].location[0][1] - Tx[0].location[0][0]) / dl_len
    dl_y = (Tx[-1].location[1][1] - Tx[0].location[1][0]) / dl_len

    # Set boundary conditions
    mesh.setCellGradBC('neumann')

    # Define the linear system needed for the DC problem. We assume an infitite
    # line source for simplicity.
    Div = mesh.faceDiv
    Grad = mesh.cellGrad
    Msig = utils.sdiag(1./(mesh.aveF2CC.T*(1./model)))

    A = Div*Msig*Grad

    # Change one corner to deal with nullspace
    A[0, 0] = 1
    A = sp.csc_matrix(A)

    # We will solve the system iteratively, so a pre-conditioner is helpful
    # This is simply a Jacobi preconditioner (inverse of the main diagonal)
    dA = A.diagonal()
    P = sp.spdiags(1/dA, 0, A.shape[0], A.shape[0])

    # Now we can solve the system for all the transmitters
    # We want to store the data
    data = []

    # There is probably a more elegant way to do this,
    # but we can just for-loop through the transmitters
    for ii in range(len(Tx)):

        start_time = time.time()  # Let's time the calculations

        # print("Transmitter %i / %i\r" % (ii+1, len(Tx)))

        # Select dipole locations for receiver
        rxloc_M = np.asarray(Rx[ii].locations[0])
        rxloc_N = np.asarray(Rx[ii].locations[1])

        # For usual cases 'dipole-dipole' or "gradient"
        if survey_type == 'pole-dipole':
            # Create an "inifinity" pole
            tx = np.squeeze(Tx[ii].location[:, 0:1])
            tinf = tx + np.array([dl_x, dl_y, 0])*dl_len*2
            inds = utils.closestPoints(mesh, np.c_[tx, tinf].T)
            RHS = (
                mesh.getInterpolationMat(np.asarray(Tx[ii]).T, 'CC').T *
                ([-1] / mesh.vol[inds])
            )
        else:
            inds = utils.closestPoints(mesh, np.asarray(Tx[ii].location))
            RHS = (
                mesh.getInterpolationMat(np.asarray(Tx[ii].location), 'CC').T *
                ([-1, 1] / mesh.vol[inds])
            )

        # Iterative Solve
        Ainvb = sp.linalg.bicgstab(P*A, P*RHS, tol=1e-5)

        # We now have the potential everywhere
        phi = utils.mkvc(Ainvb[0])

        # Solve for phi on pole locations
        P1 = mesh.getInterpolationMat(rxloc_M, 'CC')
        P2 = mesh.getInterpolationMat(rxloc_N, 'CC')

        # Compute the potential difference
        dtemp = (P1*phi - P2*phi)*np.pi

        data.append(dtemp)
        print('\rTransmitter {0} of {1} -> Time:{2} sec'.format(
            ii+1, len(Tx), time.time() - start_time)
        )

    print('Transmitter {0} of {1}'.format(ii+1, len(Tx)))
    print('Forward completed')

    # Let's just convert the 3D format into 2D (distance along line) and plot
    survey2D = convertObs_DC3D_to_2D(survey, np.ones(survey.nSrc), 'Xloc')
    data = Data(dobs=np.hstack(data), survey=survey2D)
    #survey2D.dobs = np.hstack(data)

    if not plotIt:
        return

    fig = plt.figure(figsize=(7, 7))
    ax = plt.subplot(2, 1, 1, aspect='equal')
    # Plot the location of the spheres for reference
    circle1 = plt.Circle(
        (loc[0, 0], loc[2, 0]), radi[0], color='w', fill=False, lw=3
    )
    circle2 = plt.Circle(
        (loc[0, 1], loc[2, 1]), radi[1], color='k', fill=False, lw=3
    )
    ax.add_artist(circle1)
    ax.add_artist(circle2)

    dat = mesh.plotSlice(
        np.log10(model), ax=ax, normal='Y',
        ind=indy, grid=True, clim=np.log10([sig.min(), sig.max()])
    )

    ax.set_title('3-D model')
    plt.gca().set_aspect('equal', adjustable='box')
    plt.scatter(Tx[0].location[0][0], Tx[0].location[0][2], s=40, c='g', marker='v')
    plt.scatter(Rx[0].locations[0][:, 0], Rx[0].locations[0][:, 1], s=40, c='y')
    plt.xlim([-xlim, xlim])
    plt.ylim([-zlim, mesh.vectorNz[-1]+dx])

    pos = ax.get_position()
    ax.set_position([pos.x0, pos.y0 + 0.025, pos.width, pos.height])
    pos = ax.get_position()
    # the parameters are the specified position you set
    cbarax = fig.add_axes(
        [pos.x0, pos.y0 + 0.025,  pos.width, pos.height * 0.04]
    )
    cb = fig.colorbar(
        dat[0],
        cax=cbarax,
        orientation="horizontal",
        ax=ax,
        ticks=np.linspace(np.log10(sig.min()), np.log10(sig.max()), 3),
        format="$10^{%.1f}$"
    )
    cb.set_label("Conductivity (S/m)", size=12)
    cb.ax.tick_params(labelsize=12)

    # Second plot for the predicted apparent resistivity data
    ax2 = plt.subplot(2, 1, 2, aspect='equal')

    # Plot the location of the spheres for reference
    circle1 = plt.Circle(
        (loc[0, 0], loc[2, 0]), radi[0], color='w', fill=False, lw=3
    )
    circle2 = plt.Circle(
        (loc[0, 1], loc[2, 1]), radi[1], color='k', fill=False, lw=3
    )
    ax2.add_artist(circle1)
    ax2.add_artist(circle2)

    # Add the pseudo section
    dat = plot_pseudoSection(
        data, ax2, survey_type=survey_type, data_type=unitType
    )
    ax2.set_title('Apparent Conductivity data')

    plt.ylim([-zlim, mesh.vectorNz[-1]+dx])

    return fig, ax

if __name__ == '__main__':
    run()
    plt.show()
