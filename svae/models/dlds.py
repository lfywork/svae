from __future__ import division
import autograd.numpy as np
import autograd.numpy.random as npr
import math
from autograd.misc.fixed_points import fixed_point
from svae.distributions import gamma, gaussian, univariate_gaussian
from svae.util import flat, replace, unbox

def _sample_local(local_messages, num_samples):
    fwd_messages, bwd_messages, obs_messages = local_messages
    samples = np.zeros(fwd_messages.shape[:-2])
    samples[...,0] = np.squeeze(gaussian.natural_sample(fwd_messages[...,0] + bwd_messages[...,0] + obs_messages[...,0]), -1)
    for t in xrange(1, T):
        # TODO: SAMPLE!!

def _global_kl(global_prior_natparams, global_natparams):
    stats = flat(gamma.expectedstats(global_natparams))
    natparam_difference = flat(global_natparams) - flat(global_prior_natparams)
    return np.dot(natparam_difference, stats) - (gamma.logZ(global_natparams) - gamma.logZ(global_prior_natparams))

def _local_logZ(global_stats, local_messages):
    fwd_messages, _, obs_messages = local_messages
    N, T  = fwd_messages.shape[:2]
    tau = np.concatenate(np.ones(T-1)*global_stats[0], np.array([0]))
    res = np.zeroes(N)
    v, m = univariate_gaussian.natural_to_standard(fwd_messages + obs_messages)
    for t in range(T):
        res += .5*np.log(2*np.pi)
        res -= .5*np.log(tau[t] + 1./v[:,t])
        res += .5*np.power(m[:,t], 2) * (v[:,t] + np.pow(v, 2)*tau[t])
    res

# correct up to a constant, but this means KL is not necessarily >= 0
def _prior_local_logZ(global_stats, N, T):
    return -N*T*.5*global_stats[0]

def _local_kl(global_stats, local_messages, local_stats):
    global_natparams = univariate_gaussian.pack_dense(-.5*global_stats[1], 0)
    local_natparams = math.fsum(local_messages)
    return np.tensordot(local_natparams - global_natparams, local_stats, 4) - (
        gaussian.logZ(local_natparams) - _prior_local_logZ(global_stats, local_natparams.shape[:2]))

def _local_ep_update(global_stats, encoder_potentials, (fwd_messages, bwd_messages, obs_messages)):
    N, T, C, _ = encoder_potentials[0].shape
    encoder_natparams = univariate_gaussian.pack_dense(*encoder_potentials[:2]) # (N,T,C,2)
    p = encoder_potentials[2] # (N,T,C)
    assert(fwd_messages.shape == (N,T,2))
    assert(bwd_messages.shape == (N,T,2))
    assert(obs_messages.shape == (N,T,2))

    def propagate(in_messages, t):
        cavity_natparams = fwd_messages[:,t] + bwd_messages[:,t] # (N,2)
        r = p[:,t] * np.exp(
            univariate_gaussian.logZ(encoder_natparams[:,t] + cavity_natparams) -
            univariate_gaussian.logZ(encoder_natparams[:,t])) # (N,C)
        marginal_stats = np.sum(r[...,None]*univariate_gaussian.expectedstats(
            encoder_natparams[:,t] + cavity_natparams[:,None]), axis=1) # (N,2)
        obs_natparams = univariate_gaussian.mean_to_natural(marginal_stats) - cavity_natparams # (N,2)
        out_v = univariate_gaussian.natural_to_standard(obs_natparams + in_messages[:,t])[0] + 1./global_stats[1]
        out_m = univariate_gaussian.natural_to_standard(obs_natparams + in_messages[:,t])[1]
        return obs_natparams, gaussian.standard_to_natural(out_v, out_m)

    for t in range(T-1):
        obs_message, fwd_message = propagate(fwd_messages, t)
        fwd_messages = replace(fwd_messages, fwd_message, t+1, axis=1)
        obs_messages = replace(obs_messages, obs_message, t, axis=1)
    for t in reversed(xrange(T, 0, -1)):
        obs_message, bwd_message = propagate(bwd_messages, t)
        bwd_messages = replace(bwd_messages, bwd_message, t-1, axis=1)
        obs_messages = replace(obs_messages, obs_message, t, axis=1)

    return fwd_messages, bwd_messages, obs_messages

def local_inference(global_prior_natparams, global_natparams, global_stats, encoder_potentials, n_samples):

    encoder_potentials = \
        np.squeeze(encoder_potentials[0], -1), np.squeeze(encoder_potentials[1], -1), encoder_potentials[2]

    N, T, C = encoder_potentials[0].shape

    def make_fpfun((global_stats, encoder_potentials)):
        return lambda x: \
            _local_ep_update(global_stats, encoder_potentials, x)

    def diff(a, b):
        return np.sum(np.abs(a[0]- b[0]))

    def init_x0():
        fwd_messages = univariate_gaussian.pack_dense(
            np.stack([-.5*global_stats[1]*np.ones((N,1)), -.01*np.ones((N, T-1))], axis=-1),
            np.stack([np.zeros((N,1)), .01*npr.randn((N, T-1))], axis=-1))
        bwd_messages = univariate_gaussian.pack_dense(
            np.stack([-.01*np.ones((N, T-1)), np.zeros((N,1))], axis=-1),
            np.stack([.01*npr.randn((N, T-1)), np.zeros((N,1))], axis=-1))
        obs_messages = univariate_gaussian.pack_dense(-.01*np.ones((N, T)), .01*npr.randn(N,T))
        return fwd_messages, bwd_messages, obs_messages

    local_messages = fixed_point(make_fpfun, (global_stats, encoder_potentials), init_x0(), diff, tol=1e-3)
    local_natparams = math.fsum(local_messages)
    local_stats = univariate_gaussian.expectedstats(local_natparams)

    local_samples = gaussian.natural_sample(local_natparams[1], n_samples)
    global_stats = () # TODO: add gamma stats

    # niw_stats = np.tensordot(z_pairstats, local_stats[1], [(0,1), (0,1)])
    # beta_stats = np.sum(local_stats[0], axis=0), np.sum(1-local_stats[0], axis=0)

    local_kl = _local_kl(unbox(global_stats), local_messages, local_stats)
    global_kl = _global_kl(global_prior_natparams, global_natparams)
    return local_samples, local_natparams, unbox(local_stats), global_kl, local_kl

def pgm_expectedstats(natparams):
    return gamma.expectedstats(natparams)