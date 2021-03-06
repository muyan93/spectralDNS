"""Orr-Sommerfeld"""
import pyfftw  # Hack because of https://github.com/pyFFTW/pyFFTW/issues/40
from spectralDNS import config, get_solver, solve
from spectralDNS.utilities import reset_profile
from OrrSommerfeld_shen import OrrSommerfeld
#from OrrSommerfeld_eig import OrrSommerfeld
from numpy import dot, real, pi, cos, exp, sum, zeros, arange, imag, sqrt, \
    array, zeros_like, log10, hstack
from numpy.linalg import norm
from mpiFFT4py import dct
from scipy.fftpack import ifft
import warnings
from mpi4py import MPI

try:
    import matplotlib.pyplot as plt
    import matplotlib.cbook
    warnings.filterwarnings("ignore",category=matplotlib.cbook.mplDeprecation)

except ImportError:
    warnings.warn("matplotlib not installed")
    plt = None

#params.eps = 1e-9
#def initOS(OS, U, X, t=0.):
    #for i in range(U.shape[1]):
        #x = X[0, i, 0, 0]
        #OS.interp(x, 1)
        #for j in range(U.shape[2]):
            #y = X[1, i, j, 0]
            #v = (1-x**2) + config.params.eps*dot(OS.f, real(OS.dphidy*exp(1j*(y-OS.eigval*t))))
            #u = -config.params.eps*dot(OS.f, real(1j*OS.phi*exp(1j*(y-OS.eigval*t))))
            #U[0, i, j, :] = u
            #U[1, i, j, :] = v
    #U[2] = 0

def initOS(OS, U, X, t=0.):
    x = X[0][:, 0, 0]
    phi, dphidy = OS.interp(x, 1, same_mesh=False, verbose=False)
    for j in range(U.shape[2]):
        y = X[1][0, j, 0]
        v = (1-x**2) + config.params.eps*real(dphidy*exp(1j*(y-OS.eigval*t)))
        u = -config.params.eps*real(1j*phi*exp(1j*(y-OS.eigval*t)))
        U[0, :, j, :] = u.repeat(U.shape[3]).reshape((len(x), U.shape[3]))
        U[1, :, j, :] = v.repeat(U.shape[3]).reshape((len(x), U.shape[3]))
    U[2] = 0

def dx(u, FST):
    """Compute integral of u over domain"""
    uu = sum(u, axis=(1, 2))
    N = u.shape[0]
    sl = FST.local_slice(False)[0]
    M = FST.shape()[0]
    c = zeros(M)
    cc = zeros(M)
    cc[sl] = uu
    FST.comm.Reduce(cc, c, op=MPI.SUM, root=0)
    quad = FST.bases[0].quad
    if FST.comm.Get_rank() == 0:
        if quad == 'GL':
            ak = zeros_like(c)
            ak = dct(c, ak, 1, axis=0)
            ak /= (M-1)
            w = arange(0, M, 1, dtype=float)
            w[2:] = 2./(1-w[2:]**2)
            w[0] = 1
            w[1::2] = 0
            return sum(ak*w)*config.params.L[1]*config.params.L[2]/config.params.N[1]/config.params.N[2]

        elif quad == 'GC':
            d = zeros(M)
            k = 2*(1 + arange((M-1)//2))
            d[::2] = (2./M)/hstack((1., 1.-k*k))
            w = zeros_like(d)
            w = dct(d, w, type=3, axis=0)
            return sum(c*w)*config.params.L[1]*config.params.L[2]/config.params.N[1]/config.params.N[2]
    else:
        return 0


acc = zeros(1)
OS, e0 = None, None
def initialize(solver, context):
    global OS, e0
    params = config.params
    OS = OrrSommerfeld(Re=params.Re, N=128)
    OS.solve(False)
    U = context.U
    X = context.X
    FST = context.FST
    initOS(OS, U, X)

    U_hat = solver.set_velocity(**context)
    U = solver.get_velocity(**context)

    # Compute convection from data in context (i.e., context.U_hat and context.g)
    # This is the convection at t=0
    if hasattr(context.FST, 'dx'):
        e0 = 0.5*FST.dx(U[0]**2+(U[1]-(1-X[0]**2))**2, context.ST.quad)
    else:
        e0 = 0.5*dx(U[0]**2+(U[1]-(1-X[0]**2))**2, context.FST)
    #print(e0)
    acc[0] = 0.0

    if not 'KMMRK3' in params.solver:
        # Initialize at t = dt
        context.H_hat1[:] = solver.get_convection(**context)
        initOS(OS, U, X, t=params.dt)
        U_hat = solver.set_velocity(**context)
        U = solver.get_velocity(**context)
        context.U_hat0[:] = U_hat
        params.t = params.dt
        params.tstep = 1
        if hasattr(context.FST, 'dx'):
            e1 = 0.5*FST.dx(U[0]**2+(U[1]-(1-X[0]**2))**2, context.ST.quad)
        else:
            e1 = 0.5*dx(U[0]**2+(U[1]-(1-X[0]**2))**2, context.FST)

        if solver.rank == 0:
            acc[0] += abs(e1/e0 - exp(2*imag(OS.eigval)*params.t))

    else:
        params.t = 0
        params.tstep = 0

    if not "KMM" in params.solver:
        P_hat = solver.compute_pressure(**context)
        P = FST.backward(P_hat, context.P, context.SN)

    else:
        context.g[:] = 0


    #HH = context.work[(U[0], 0)]
    #HH = context.FST.backward(context.H_hat1[1], HH)
    #plt.figure()
    #plt.contourf(context.X[1][:,:,0], context.X[0][:,:,0], HH[:,:,0], 100)
    #plt.colorbar(im1)
    #plt.show()


def set_Source(Source, Sk, FST, ST, N, **kw):
    Source[:] = 0
    Source[1] = -2./config.params.Re
    Sk[:] = 0
    if hasattr(FST, 'complex_shape'):
        Sk[1] = FST.scalar_product(Source[1], Sk[1], ST)

    else:
        Sk[1] = FST.scalar_product(Source[1], Sk[1])
        Sk[1] /= (4*pi**2)
    Sk[1, -2:,0,0] = 0

im1, im2, im3, im4 = (None, )*4
def update(context):

    c = context
    params = config.params
    solver = config.solver

    #if params.tstep == 2: reset_profile(profile)

    if (params.tstep % params.plot_step == 0 or
        params.tstep % params.compute_energy == 0):
        U = solver.get_velocity(**context)

    #U = solver.get_velocity(**context)
    #print(norm(context.U_hat[0]), norm(context.U_hat[1]))
    #print(norm(context.U[0]), norm(context.U[1]))

    # Use GL for postprocessing
    global im1, im2, im3, OS, e0, acc
    if not plt is None:
        if im1 is None and solver.rank == 0 and params.plot_step > 0:
            plt.figure()
            im1 = plt.contourf(c.X[1][:,:,0], c.X[0][:,:,0], c.U[0,:,:,0], 100)
            plt.colorbar(im1)
            plt.draw()

            plt.figure()
            im2 = plt.contourf(c.X[1][:,:,0], c.X[0][:,:,0], c.U[1,:,:,0] - (1-c.X[0][:,:,0]**2), 100)
            plt.colorbar(im2)
            plt.draw()

            plt.figure()
            im3 = plt.quiver(c.X[1][:,:,0], c.X[0][:,:,0], c.U[1,:,:,0]-(1-c.X[0][:,:,0]**2), c.U[0,:,:,0])
            plt.draw()

            plt.pause(1e-6)

        if params.tstep % params.plot_step == 0 and solver.rank == 0 and params.plot_step > 0:
            im1.ax.clear()
            im1.ax.contourf(c.X[1][:,:,0], c.X[0][:,:,0], U[0, :, :, 0], 100)
            im1.autoscale()
            im2.ax.clear()
            im2.ax.contourf(c.X[1][:,:,0], c.X[0][:,:,0], U[1, :, :, 0]-(1-c.X[0][:,:,0]**2), 100)
            im2.autoscale()
            im3.set_UVC(U[1,:,:,0]-(1-c.X[0][:,:,0]**2), U[0,:,:,0])
            plt.pause(1e-6)

    if params.tstep % params.compute_energy == 0:
        e1, e2, exact = compute_error(c)
        if solver.rank == 0 and not config.params.spatial_refinement_test:
            acc[0] += abs(e1/e0-exact)
            print("Time %2.5f Norms %2.16e %2.16e %2.16e %2.16e" %(params.t, e1/e0, exact, e1/e0-exact, sqrt(e2)))

def compute_error(context):
    global OS, e0, acc
    c = context
    params = config.params
    solver = config.solver
    U = solver.get_velocity(**c)
    pert = (U[1] - (1-c.X[0]**2))**2 + U[0]**2
    if hasattr(context.FST, 'dx'):
        e1 = 0.5*c.FST.dx(pert, c.ST.quad)
    else:
        e1 = 0.5*dx(pert, c.FST)

    exact = exp(2*imag(OS.eigval)*params.t)
    U0 = c.work[(c.U, 0)]
    initOS(OS, U0, c.X, t=params.t)
    #pert = (U[0] - U0[0])**2 + (U[1]-U0[1])**2
    pert = (U[0] - U0[0])**2
    if hasattr(context.FST, 'dx'):
        e2 = 0.5*c.FST.dx(pert, c.ST.quad)
    else:
        e2 = 0.5*dx(pert, c.FST)

    return e1, e2, exact

def regression_test(context):
    e1, e2, exact = compute_error(context)
    if config.solver.rank == 0:
        assert sqrt(e2) < 1e-12

def refinement_test(context):
    e1, e2, exact = compute_error(context)
    if config.solver.rank == 0:
        print("Computed error = %2.8e %2.8e %2.8e" %(sqrt(e2)/config.params.eps, config.params.dt, config.params.eps))

def eps_refinement_test(context):
    e1, e2, exact = compute_error(context)
    if config.solver.rank == 0:
        print(" %2d & %2.8e & %2.8e \\\ " %(-int(log10(config.params.eps)), sqrt(e2)/config.params.eps, e1/e0-exact))

def spatial_refinement_test(context):
    e1, e2, exact = compute_error(context)
    if config.solver.rank == 0:
        print(" %2d & %2.8e & %2.8e \\\ " %(2**config.params.M[0], sqrt(e2)/config.params.eps, acc[0]))

if __name__ == "__main__":
    config.update(
        {
        'Re': 8000.,
        'nu': 1./8000.,             # Viscosity
        'dt': 0.001,                 # Time step
        'T': 0.01,                   # End time
        'L': [2, 2*pi, pi],
        'M': [7, 5, 2],
        'Dquad': 'GC',
        'Bquad': 'GC',
        'dealias': None
        },  "channel"
    )
    config.channel.add_argument("--compute_energy", type=int, default=1)
    config.channel.add_argument("--plot_step", type=int, default=1)
    config.channel.add_argument("--refinement_test", type=bool, default=False)
    config.channel.add_argument("--eps_refinement_test", type=bool, default=False)
    config.channel.add_argument("--spatial_refinement_test", type=bool, default=False)
    config.channel.add_argument("--eps", type=float, default=1e-7)
    #solver = get_solver(update=update, regression_test=regression_test, mesh="channel")
    solver = get_solver(update=update, mesh="channel")

    if config.params.eps_refinement_test:
        print("eps refinement-test")
        solver.update = lambda x: None
        solver.regression_test = eps_refinement_test
        config.params.verbose = False
        context = solver.get_context()

        for eps in [1e-4, 1e-5, 1e-6, 1e-7, 1e-8, 1e-9, 1e-10, 1e-11, 1e-12]:
            config.params.eps = eps
            initialize(solver, context)
            set_Source(**context)
            solve(solver, context)

    elif config.params.spatial_refinement_test:
        print("spatial refinement-test")
        def update(con):
            e1, e2, exact = compute_error(con)
            acc[0] += abs(e1/e0-exact)
        solver.update = update
        solver.regression_test = spatial_refinement_test
        config.params.verbose=False
        for M in [4, 5, 6, 7, 8]:
            config.params.M = [M, 3, 1]
            context = solver.get_context()
            initialize(solver, context)
            set_Source(**context)
            solve(solver, context)

    else:
        if config.params.refinement_test:
            solver.update = lambda x: None
            solver.regression_test = refinement_test
        context = solver.get_context()
        initialize(solver, context)
        set_Source(**context)
        solve(solver, context)
        #c = context
        #plt.figure()
        #plt.contourf(c.X[1][:,:,0], c.X[0][:,:,0], c.U[0,:,:,0], 100)
        #plt.colorbar()
        #plt.figure()
        #plt.contourf(c.X[1][:,:,0], c.X[0][:,:,0], c.U[1,:,:,0], 100)
        #plt.colorbar()

        #plt.show()
