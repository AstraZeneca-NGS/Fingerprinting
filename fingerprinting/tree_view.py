from __future__ import print_function
import json

import os
import re
import subprocess
import time
from sys import platform

import sys
from os.path import join, dirname
from collections import defaultdict
from os.path import abspath, join, dirname, splitext, basename

from Bio import SeqIO, Phylo
from flask import Flask, render_template, abort, request

from ngs_utils import logger as log
from ngs_utils.bed_utils import Region
from ngs_utils.file_utils import safe_mkdir, file_transaction, can_reuse, verify_file
from ngs_utils.file_utils import can_reuse, safe_mkdir

from fingerprinting import config
from fingerprinting.model import Project, db, Sample, Run, get_or_create_run
from fingerprinting.utils import read_fasta, get_sample_and_project_name, \
    FASTA_ID_PROJECT_SEPARATOR, calculate_distance_matrix
from fingerprinting.lookups import get_snp_record, get_sample_by_name

suffix = 'lnx' if 'linux' in platform else 'osx'
prank_bin = join(dirname(__file__), 'prank', 'prank_' + suffix, 'bin', 'prank')


# PROJ_COLORS = ['#000000', '#90ed7d', '#f7a35c', '#8085e9', '#f15c80',
#                '#e4d354', '#2b908f', '#f45b5b', '#91e8e1', '#7cb5ec']
PROJ_COLORS = [
    '#000000',
    '#1f78b4',
    '#b2df8a',
    '#33a02c',
    '#fb9a99',
    '#e31a1c',
    '#fdbf6f',
    '#ff7f00',
    '#cab2d6',
    '#6a3d9a',
    '#ffff99',
    '#b15928',
]


def run_analysis_socket_handler(run_id):
    log.debug('Recieved request to start analysis for ' + run_id)
    ws = request.environ.get('wsgi.websocket', None)
    if not ws:
        raise RuntimeError('Environment lacks WSGI WebSocket support')

    def _run_cmd(cmdl):
        log.debug(cmdl)
        proc = subprocess.Popen(cmdl.split(), stderr=subprocess.STDOUT, stdout=subprocess.PIPE, env=os.environ)
        # lines = []
        # prev_time = time.time()
        for stdout_line in iter(proc.stdout.readline, ''):
            # lines.append(stdout_line)
            # cur_time = time.time()
            # if cur_time - prev_time > 2:
            if '#(' not in stdout_line.strip():
                _send_line(ws, stdout_line)
            # lines = []

    _send_line(ws, '')
    _send_line(ws, 'Genotyping using VarDict...')
    _run_cmd(sys.executable + ' manage.py analyse_projects ' + run_id)
    run = Run.query.get(run_id)
    if not run:
        raise RuntimeError('Genotyping failed to run')
    
    prank_out = join(run.work_dir, splitext(basename(run.fasta_file))[0])
    _send_line(ws, '')
    _send_line(ws, 'Building phylogeny tree using prank...')
    _run_cmd(prank_bin + ' -d=' + run.fasta_file + ' -o=' + prank_out + ' -showtree')
    if not verify_file(prank_out + '.best.dnd'):
        raise RuntimeError('Prank failed to run')
    os.rename(prank_out + '.best.dnd', run.tree_file)
    os.remove(prank_out + '.best.fas')
    ws.send(json.dumps({'finished': True}))
    return ''


def _send_line(ws, line):
    log.debug(line.rstrip())
    ws.send(json.dumps({
        'line': line.rstrip(),
    }))


def render_phylo_tree_page(run_id):
    run = Run.query.filter_by(id=run_id).first()
    if not run or not can_reuse(run.tree_file, run.fasta_file):
        return render_template(
            'processing.html',
            projects=run_id.split(','),
            run_id=run_id,
            title='Processing ' + ', '.join(run_id.split(',')),
        )

    log.debug('Prank results found, rendering tree!')
    seq_by_id = read_fasta(run.fasta_file)
    info_by_sample_by_project = dict()
    for i, p in enumerate(run.projects):
        info_by_sample_by_project[p.name] = dict()
        info_by_sample_by_project[p.name]['name'] = p.name
        info_by_sample_by_project[p.name]['color'] = PROJ_COLORS[i % len(PROJ_COLORS)]
        info_by_sample_by_project[p.name]['samples'] = dict()
        for s in p.samples:
            info_by_sample_by_project[p.name]['samples'][s.name] = {
                'name': s.name,
                'id': s.id,
                'sex': s.sex,
                'seq': [nt for nt in seq_by_id[s.name + FASTA_ID_PROJECT_SEPARATOR + p.name]],
            }
    all_samples_count = sum(len(p.samples.all()) for p in run.projects)
    locations = [dict(
            chrom=l.chrom.replace('chr', ''),
            pos=l.pos,
            rsid=l.rsid,
            gene=l.gene,
            index=l.index)
        for l in run.locations]
    return render_template(
        'tree.html',
        projects=[{
            'name': str(p.name),
            'color': PROJ_COLORS[i % len(PROJ_COLORS)],
            'samples': [str(sample.name) for sample in p.samples],
            'ids': [str(sample.id) for sample in p.samples]
        } for i, p in enumerate(run.projects)],
        title=', '.join(p.name for p in run.projects),
        tree_newick=open(run.tree_file).read(),
        info_by_sample_by_project=json.dumps(info_by_sample_by_project),
        samples_count=all_samples_count,
        locations=json.dumps(locations)
    )


