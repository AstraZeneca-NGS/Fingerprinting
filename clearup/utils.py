import glob
import shutil
from collections import defaultdict, OrderedDict
import os
from os.path import join, dirname, splitext, basename, isfile, isdir
from subprocess import check_output

import genomepy
from Bio import SeqIO

from ngs_utils import logger
from ngs_utils import call_process
from ngs_utils.file_utils import verify_file, safe_mkdir, file_transaction, which, can_reuse
from ngs_utils.sambamba import index_bam, call_sambamba
from ngs_utils.utils import is_az

from clearup import DATA_DIR


FASTA_ID_PROJECT_SEPARATOR = '____PROJECT_'


def get_ref_fasta(genome):
    if is_az():
        path = '/ngs/reference_data/genomes/Hsapiens/' + genome + '/seq/' + genome + '.fa'
        if isfile(path):
            logger.info('Found genome fasta at ' + path)
            return path

    if isdir(join(DATA_DIR, 'genomes', genome)):
        genome_dir = safe_mkdir(join(DATA_DIR, 'genomes'))
    else:
        genome_dir = safe_mkdir(join(DATA_DIR, '..', 'genomes'))
    if genome not in genomepy.list_installed_genomes(genome_dir):
        genome_rec = [rec for rec in genomepy.list_available_genomes() if rec[1] == genome]
        if genome_rec:
            genome_rec = genome_rec[0]
        else:
            logger.critical('Error: genome ' + genome + ' is not available')
        logger.info('Downloading genome ' + genome + ' from ' + genome_rec[1] +
                    ' and installing into ' + genome_dir)
        genomepy.install_genome(genome, 'UCSC', genome_dir=genome_dir)
    genome_fasta_file = genomepy.Genome(genome, genome_dir=genome_dir).filename
    return genome_fasta_file


def read_fasta(fasta_fpath):
    seq_by_id = OrderedDict()
    with open(fasta_fpath) as f:
        records = SeqIO.parse(f, 'fasta')
        for record in records:
            seq_by_id[record.id] = record.seq
    return seq_by_id


# def load_bam_file(bam_file, bams_dir, locations, sample_name):
#     """ Slicing to fingerprints locations
#     """
#     sample_bams_dir = safe_mkdir(join(bams_dir, sample_name))
#
#     bam_index_file = bam_file + '.bai'
#     if not verify_file(bam_index_file):
#         logger.error('BAM index file not found in ' + bam_index_file)
#         raise RuntimeError
#
#     for loc in locations:
#         sliced_bam = join(sample_bams_dir, sample_name + '_' + loc.chrom + '_' + str(loc.pos) + '.bam')
#         call_process.run('sambamba slice {bam_file} -o {sliced_bam} {loc.chrom}:{loc.pos}'.format(**locals()))


def load_bam_file(bam_file, bams_dir, snp_bed, sample_name):
    """ Slicing to fingerprints locations
    """
    sliced_bam_file = join(bams_dir, sample_name + '.bam')
    if not can_reuse(sliced_bam_file, [bam_file, snp_bed]):
        cmdl = 'view {bam_file} -L {snp_bed} -F "not duplicate" -f bam'.format(**locals())
        call_sambamba(cmdl, bam_fpath=bam_file, output_fpath=sliced_bam_file)
        # index_bam(sliced_bam_file)
    return sliced_bam_file


def get_sample_and_project_name(name, fingerprint_project=None):
    project_names = fingerprint_project.split(',') if fingerprint_project else []
    if FASTA_ID_PROJECT_SEPARATOR in name:
        sample_name, project_name = name.split(FASTA_ID_PROJECT_SEPARATOR)
    else:
        sample_name, project_name = name, ''
    if len(project_names) != 1:
        return sample_name, project_name
    else:
        return sample_name, project_names[0]


def is_sex_chrom(chrom):
    return chrom in ['X', 'Y', 'chrX', 'chrY']


def bam_samplename(bam_file):
    # return check_output('goleft samplename ' + bam_file)
    return splitext(basename(bam_file))[0]
