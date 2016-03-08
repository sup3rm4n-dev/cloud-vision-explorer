#!/usr/bin/env python

"""
A simple Python wrapper for the bh_tsne binary that makes it easier to use it
for TSV files in a pipeline without any shell script trickery.

Note: The script does some minimal sanity checking of the input, but don't
    expect it to cover all cases. After all, it is a just a wrapper.

Example:

    > echo -e '1.0\t0.0\n0.0\t1.0' | ./bhtsne.py -d 2 -p 0.1
    -2458.83181442  -6525.87718385
    2458.83181442   6525.87718385

The output will not be normalised, maybe the below one-liner is of interest?:

    python -c 'import numpy;  from sys import stdin, stdout;
        d = numpy.loadtxt(stdin); d -= d.min(axis=0); d /= d.max(axis=0);
        numpy.savetxt(stdout, d, fmt="%.8f", delimiter="\t")'

Author:     Pontus Stenetorp    <pontus stenetorp se>
Author:     Philippe Remy    <premy@reactive.co.jp>

Version:    2016-03-08
"""

# Copyright (c) 2013, Pontus Stenetorp <pontus stenetorp se>
# Copyright (c) 2016, Philippe Remy <premy@reactive.co.jp>
#
# Permission to use, copy, modify, and/or distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
# WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
# ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
# WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
# ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
# OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

from argparse import ArgumentParser, FileType
import numpy as np
from tempfile import mkdtemp
from os.path import abspath, dirname, isfile, join as path_join
from struct import calcsize, pack, unpack
from sys import stderr, stdin, stdout
from platform import system
from os import devnull
from shutil import rmtree
from subprocess import Popen

IS_WINDOWS = True if system() == 'Windows' else False

BH_TSNE_BIN_PATH = path_join(dirname(__file__), 'windows', 'bhtsne/bh_tsne.exe') if IS_WINDOWS else path_join(
    dirname(__file__), 'bhtsne/bh_tsne')
assert isfile(BH_TSNE_BIN_PATH), ('Unable to find the bh_tsne binary in the '
                                  'same directory as this script, have you forgotten to compile it?: {}'
                                  ).format(BH_TSNE_BIN_PATH)

DEFAULT_VERBOSE = True
DEFAULT_OUTPUT_DIMS = 2
DEFAULT_INITIAL_DIMS_AFTER_PCA = 50
DEFAULT_PERPLEXITY = 50
DEFAULT_THETA = 0.5  # 0.0 for theta is equivalent to vanilla t-SNE
EMPTY_SEED = 1


def arg_parse():
    arg_parse = ArgumentParser('bh_tsne python wrapper')
    arg_parse.add_argument('-d', '--no_dims', type=int,
                           default=DEFAULT_OUTPUT_DIMS)
    arg_parse.add_argument('-p', '--perplexity', type=float,
                           default=DEFAULT_PERPLEXITY)
    arg_parse.add_argument('-n', '--initial_dims', type=float,
                           default=DEFAULT_INITIAL_DIMS_AFTER_PCA)
    arg_parse.add_argument('-t', '--theta', type=float, default=DEFAULT_THETA)
    arg_parse.add_argument('-r', '--randseed', type=int, default=EMPTY_SEED)
    arg_parse.add_argument('-v', '--verbose', type=bool, default=DEFAULT_VERBOSE)
    arg_parse.add_argument('-i', '--input', type=FileType('r'), default=stdin)
    arg_parse.add_argument('-o', '--output', type=FileType('w'),
                           default=stdout)
    return arg_parse


class TmpDir:
    def __init__(self):
        pass

    def __enter__(self):
        self._tmp_dir_path = mkdtemp()
        return self._tmp_dir_path

    def __exit__(self, type, value, traceback):
        rmtree(self._tmp_dir_path)


def _read_unpack(fmt, fh):
    return unpack(fmt, fh.read(calcsize(fmt)))


def fast_tsne(X, initial_dims=DEFAULT_INITIAL_DIMS_AFTER_PCA, no_dims=DEFAULT_OUTPUT_DIMS, perplexity=DEFAULT_PERPLEXITY,
              theta=DEFAULT_THETA, rand_seed=EMPTY_SEED, verbose=DEFAULT_VERBOSE):

    # Perform the initial dimensionality reduction using PCA
    X -= np.mean(X, axis=0)
    cov_x = np.dot(np.transpose(X), X)
    [eig_val, eig_vec] = np.linalg.eig(cov_x)

    # sort the eigenvalues desc.
    ev_list = zip(eig_val, eig_vec)
    ev_list.sort(key=lambda tup: tup[0], reverse=True)
    eig_val, eig_vec = zip(*ev_list)
    eig_vec = np.array(eig_vec)

    if initial_dims > len(eig_vec):
        initial_dims = len(eig_vec)

    eig_vec = eig_vec[:, :initial_dims]
    X = np.dot(X, eig_vec)

    sample_dim = len(X[0])
    sample_count = len(X)

    with TmpDir() as tmp_dir_path:
        with open(path_join(tmp_dir_path, 'data.dat'), 'wb') as data_file:
            # Write the bh_tsne header
            data_file.write(pack('iiddi', sample_count, sample_dim, theta, perplexity, no_dims))
            # Then write the data
            for sample in X:
                data_file.write(pack('{}d'.format(len(sample)), *sample))
            # Write random seed if specified
            if rand_seed != EMPTY_SEED:
                data_file.write(pack('i', rand_seed))

        # Call bh_tsne and let it do its thing
        with open(devnull, 'w') as dev_null:
            bh_tsne_p = Popen((abspath(BH_TSNE_BIN_PATH),), cwd=tmp_dir_path,
                              # bh_tsne is very noisy on stdout, tell it to use stderr
                              #   if it is to print any output
                              stdout=stderr if verbose else dev_null)
            bh_tsne_p.wait()
            assert not bh_tsne_p.returncode, ('ERROR: Call to bh_tsne exited '
                                              'with a non-zero return code exit status, please ' +
                                              ('enable verbose mode and ' if not verbose else '') +
                                              'refer to the bh_tsne output for further details')

        # Read and pass on the results
        with open(path_join(tmp_dir_path, 'result.dat'), 'rb') as output_file:
            # The first two integers are just the number of samples and the
            #   dimensionality
            result_samples, result_dims = _read_unpack('ii', output_file)
            # Collect the results, but they may be out of order
            results = [_read_unpack('{}d'.format(result_dims), output_file)
                       for _ in xrange(result_samples)]
            # Now collect the landmark data so that we can return the data in the order it arrived
            results = [(_read_unpack('i', output_file), e) for e in results]
            # Put the results in order and yield it
            results.sort()
            for _, result in results:
                yield result  # lazy return.
                # The last piece of data is the cost for each sample, we ignore it
                # read_unpack('{}d'.format(sample_count), output_file)


def main(args):
    arg_p = arg_parse().parse_args(args[1:])
    data = np.loadtxt(arg_p.input)
    for result in fast_tsne(data,
                            no_dims=arg_p.no_dims,
                            initial_dims=arg_p.initial_dims,
                            perplexity=arg_p.perplexity, theta=arg_p.theta,
                            rand_seed=arg_p.randseed,
                            verbose=arg_p.verbose):
        fmt = ''
        for i in range(1, len(result)):
            fmt += '{}\t'
        fmt += '{}\n'
        arg_p.output.write(fmt.format(*result))


if __name__ == "__main__":
    from sys import argv
    exit(main(argv))
    # tsne_python_file = open('tsne_data.dat', 'r')
    # python_dict = pickle.load(tsne_python_file)
    # X = np.array(python_dict['data'])
    # for result in tsne(X):
    #    print result
