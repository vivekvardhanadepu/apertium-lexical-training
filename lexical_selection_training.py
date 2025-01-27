#!/usr/bin/env python3

# lexical training script
import os
import sys
import gzip
import shutil

from subprocess import Popen, PIPE, call
from check_config import check_config, irstlm_path, get_modes, get_autobil, get_mode_after_biltrans
from clean_corpus import clean_corpus
from importlib import import_module
from contextlib import redirect_stdout, redirect_stderr
from typing import List


def query(question, default="yes"):
    """Ask a yes/no question via raw_input() and return their answer.

    "question" is a string that is presented to the user.
    "default" is the presumed answer if the user just hits <Enter>.
            It must be "yes" (the default), "no" or None (meaning
            an answer is required of the user).

    The "answer" return value is True for "yes" or False for "no".
    """
    valid = {"yes": True, "y": True, "ye": True, "no": False, "n": False}
    if default is None:
        prompt = " [y/n]"
    elif default == "no":
        prompt = " [y/N]"
    else:
        prompt = " [Y/n]"
        default = "yes"

    while True:
        print(f"{question} {prompt} (default='{default}')?")
        choice = input().lower()
        if default is not None and choice == "":
            return valid[default]
        elif choice in valid:
            return valid[choice]
        else:
            print("Please respond with 'yes', 'no', 'y' or 'n'")


def pipe(cmds, firstin, lastout, stderr, expected_lines=None):
    """Open a list of commands as a simple shell pipe, using the same stderr
    for all commands.
    Returns None if the command list is empty.
    If 'expected_lines' is given and 'pv' is installed, we get a progress bar.
    Example usage:
    >>> cmds = [['yes', 'olleh'], ['head', '-2'], ['rev']]
    >>> with open('/tmp/foo', 'w') as outf:
            p = pipe(cmds, None, outf, sys.stderr)
            retcode = p.wait()
    >>> print(open('/tmp/foo', 'r').read())
    hello
    hello
    """
    if cmds == []:
        return
    lasterr = stderr
    if expected_lines and shutil.which("pv"):
        cmds += [['pv', '-l', '-s', str(expected_lines)]]
        lasterr = sys.stderr
    procs = []                  # type: List[Popen]
    for i in range(len(cmds)):
        cmd = cmds[i]
        inp = procs[i-1].stdout if i > 0 else firstin
        outp = PIPE if i+1 < len(cmds) else lastout
        errp = stderr if i+1 < len(cmds) else lasterr
        procs.append(Popen(cmd, stdin=inp, stdout=outp, stderr=errp))
    return procs[-1]


def parallel_training(config, cache_dir, log):

    MIN = 1

    # file names
    sl_tagged = os.path.join(
        cache_dir, f"{config['CORPUS']}.tagged.{config['SL']}")
    tl_tagged = os.path.join(
        cache_dir, f"{config['CORPUS']}.tagged.{config['TL']}")
    lines = os.path.join(cache_dir, f"{config['CORPUS']}.lines")
    tagged_merged = os.path.join(
        cache_dir, f"{config['CORPUS']}.tagged-merged.{config['SL']}-{config['TL']}")
    alignment = os.path.join(
        cache_dir, f"{config['CORPUS']}.align.{config['SL']}-{config['TL']}")
    clean_biltrans = os.path.join(
        cache_dir, f"{config['CORPUS']}.clean_biltrans.{config['SL']}-{config['TL']}")
    phrasetable = os.path.join(
        cache_dir, f"{config['CORPUS']}.phrasetable.{config['SL']}-{config['TL']}")
    candidates = os.path.join(
        cache_dir, f"{config['CORPUS']}.candidates.{config['SL']}-{config['TL']}")
    freq_lex = os.path.join(
        cache_dir, f"{config['CORPUS']}.lex.{config['SL']}-{config['TL']}")
    ngrams = os.path.join(
        cache_dir, 'ngrams')
    # events = os.path.join(
    #     cache_dir, 'events')
    # events_trimmed = os.path.join(
    #     cache_dir, 'events.trimmed')
    # lambdas = os.path.join(
    #     cache_dir, 'lambdas')
    # rules_all = os.path.join(
    #     cache_dir, 'rules_all.txt')
    # ngrams_all = os.path.join(
    #     cache_dir, 'ngrams_all.txt')
    rules = f"{config['CORPUS']}.{config['SL']}-{config['TL']}.ngrams-lm-{MIN}.lrx"

    if os.path.isfile(rules):
        if not query(f"Do you want to overwrite '{rules}'"):
            print(f"(re)move {rules} and re-run lexical_training.py")
            exit(1)
        os.remove(rules)

    with open(config['CORPUS_SL'], 'r') as corpus_sl:
        training_lines = len(corpus_sl.readlines())
        if config['TRAINING_LINES'] > training_lines:
            print(
                f"Warning: {config['TRAINING_LINES']}(TRAINING_LINES) > {training_lines}")
        else:
            training_lines = config['TRAINING_LINES']

    print(f"Using {training_lines} lines from the corpora")

    print("Tagging the source side corpus ...")
    cmds = [['head', '-n', str(training_lines)],
            ['tr', '\\/$^@', '?'],  # clean corpus
            ['apertium', '-d', config['LANG_DATA'],
             f"{config['SL']}-{config['TL']}-tagger"],
            ['apertium-pretransfer']]
    with open(config['CORPUS_SL']) as inp, open(sl_tagged, 'w') as outp:
        pipe(cmds, inp, outp, log, training_lines).wait()

    print("Tagging the target side corpus ...")
    cmds = [['head', '-n', str(training_lines)],
            ['tr', '\\/$^@', '?'],  # clean corpus
            ['apertium', '-d', config['LANG_DATA'],
             f"{config['TL']}-{config['SL']}-tagger"],
            ['apertium-pretransfer']]
    with open(config['CORPUS_TL']) as inp, open(tl_tagged, 'w') as outp:
        pipe(cmds, inp, outp, log, training_lines).wait()

    print("Combining tagged corpora ...")
    # removing lines with no analyses
    with open(lines, 'w') as f:
        call(['seq', '1', str(training_lines)],
             stdout=f, stderr=log)

    clean_tagged = os.path.join(
        cache_dir, f"{config['CORPUS']}.clean_tagged")
    with open(clean_tagged, 'w') as f1:
        cmds = [['paste', lines, sl_tagged, tl_tagged],
                ['grep', '<*\t*<']]
        pipe(cmds, None, f1, log).wait()

    with open(clean_tagged, 'r') as f0:
        with open(lines, 'w') as f1:
            call(['cut', '-f', '1'], stdin=f0, stdout=f1, stderr=log)

        f0.seek(0)
        with open(sl_tagged, 'w') as f2:
            cmds = [['cut', '-f', '2'],
                    ['sed', 's/ /~~/g'],
                    ['sed', r's/\$[^\^]*/$ /g']]
            pipe(cmds, f0, f2, log).wait()

        f0.seek(0)
        with open(tl_tagged, 'w') as f2:
            cmds = [['cut', '-f', '3'],
                    ['sed', 's/ /~~/g'],
                    ['sed', r's/\$[^\^]*/$ /g']]
            pipe(cmds, f0, f2, log).wait()

    os.remove(clean_tagged)

    with open(tagged_merged, 'w') as f:
        with open(os.devnull, 'r') as f1:
            call(['paste', '-d', '||| ', tl_tagged, '-', '-', '-',
                  sl_tagged], stdin=f1, stdout=f, stderr=log)

    print("Aligning parallel corpus ...")
    with open(alignment, 'w') as f:
        call([config['FAST_ALIGN'], '-i', tagged_merged, '-d', '-o', '-v'],
             stdout=f,
             stderr=log)

    with open(sl_tagged, 'r+') as f:
        data = f.read()
        f.seek(0)
        f.write(data.replace('~~', ' '))

    with open(tl_tagged, 'r+') as f:
        data = f.read()
        f.seek(0)
        f.write(data.replace('~~', ' '))

    # temp files
    tmp1 = 'tmp1'
    tmp2 = 'tmp2'

    print("Processing tagger output and creating phrase table ...")
    modes = get_modes(config['LANG_DATA'])
    with open(tmp1, 'w') as f1, open(tmp2, 'w') as f2:
        sl_tl_autobil = get_autobil(modes, config['LANG_DATA'], config['PAIR'])
        tl_sl_autobil = get_autobil(modes, config['LANG_DATA'], config['REVERSE_PAIR'])
        with open(tl_tagged, 'r') as f:
            # call([os.path.join(config['LEX_TOOLS'], 'process-tagger-output'),
            call(['process-tagger-output', tl_sl_autobil],
                 stdin=f, stdout=f1, stderr=log)
        with open(sl_tagged, 'r') as f:
            # call([os.path.join(config['LEX_TOOLS'], 'process-tagger-output'),
            call(['process-tagger-output', sl_tl_autobil],
                 stdin=f, stdout=f2, stderr=log)
            f.seek(0)
            with open(clean_biltrans, 'w') as f0:
                # call([os.path.join(config['LEX_TOOLS'], 'process-tagger-output'),
                call(['process-tagger-output', sl_tl_autobil],
                     stdin=f, stdout=f0, stderr=log)

    cmds = [['paste', tmp1, tmp2, alignment], ['sed', 's/\t/ ||| /g']]
    with open(phrasetable, 'w') as f:
        pipe(cmds, None, f, log).wait()

    os.remove(tmp1)
    os.remove(tmp2)

    print("Turning aligned ngrams into rules ...")
    # extract sentences
    mod = import_module('extract-sentences')
    extract_sentences = getattr(mod, 'extract_sentences')
    with open(candidates, 'w') as f, redirect_stdout(f), redirect_stderr(log):
        extract_sentences(phrasetable, clean_biltrans)

    # extract freq lexicon
    mod = import_module('extract-freq-lexicon')
    extract_freq_lexicon = getattr(mod, 'extract_freq_lexicon')
    with open(freq_lex, 'w') as f, redirect_stdout(f), redirect_stderr(log):
        extract_freq_lexicon(candidates)

    # count patterns
    mod = import_module('ngram-count-patterns')
    ngram_count_patterns = getattr(mod, 'ngram_count_patterns')
    with open(ngrams, 'w') as f, redirect_stdout(f), redirect_stderr(log):
        ngram_count_patterns(freq_lex, candidates, config['CRISPHOLD'], config['MAX_RULES'])

    # ngrams to rules
    mod = import_module('ngrams-to-rules')
    ngrams_to_rules = getattr(mod, 'ngrams_to_rules')
    with open(rules, 'w') as f, redirect_stdout(f), redirect_stderr(log):
        ngrams_to_rules(ngrams, config['CRISPHOLD'])
    return rules

    # # count patterns
    # mod = import_module('ngram-count-patterns-maxent2')
    # ngram_count_patterns = getattr(mod, 'ngram_count_patterns')
    # with open(ngrams, 'w') as f1, open(events, 'w') as f2, redirect_stdout(f2), redirect_stderr(f1):
    #     ngram_count_patterns(freq_lex, candidates)

    # # print("hello")
    # with open(events, 'r') as f1, open(events_trimmed, 'w') as f2:
    #     call(['grep', '-v', '-e', '\$ 0\.0 #', '-e', '\$ 0 #'],
    #          stdin=f1, stdout=f2, stderr=log)
    # # print("world")

    # with open(events_trimmed, 'r') as f:
    #     cmds = [['cut', '-f', '1'], ['sort', '-u']]  # ,
    #     # ['sed', 's/[\*\^\$]/\\\\\1/g']]
    #     with open('tmp.sl', 'w') as f0:
    #         pipe(cmds, f, f0, log).wait()

    # # extracting lambdas with yasmet
    # with open('tmp.sl', 'r') as f:
    #     temp_lambdas = f.read()
    #     with open(events_trimmed, 'r') as f0, open('tmp.yasmet', 'a+') as f1, open(lambdas, 'a') as f2:
    #         f2.truncate(0)
    #         for l in temp_lambdas.split('\n')[:-1]:
    #             f0.seek(0)
    #             f1.truncate(0)
    #             # print(l)
    #             cmds = [['grep', f'^{l}'], ['head', '-1'], ['cut', '-f', '2']]
    #             pipe(cmds, f0, f1, log).wait()
    #             f0.seek(0)

    #             cmds = [['grep', f'^{l}'], ['cut', '-f', '3']]
    #             pipe(cmds, f0, f1, log).wait()
    #             f1.seek(0)

    #             cmds = [
    #                 ['yasmet', '-red', str(MIN)], ['yasmet'], ['sed', 's/ /\t/g'], ['sed', f's/^/{l}\t/g']]
    #             pipe(cmds, f1, f2, log).wait()

    # os.remove('tmp.yasmet')
    # os.remove('tmp.sl')

    # # merge ngrams lambdas
    # mod = import_module('merge-ngrams-lambdas')
    # merge_ngrams_lambdas = getattr(mod, 'merge_ngrams_lambdas')
    # with open(rules_all, 'w') as f, redirect_stdout(f), redirect_stderr(log):
    #     merge_ngrams_lambdas(ngrams, lambdas)

    # # lambdas to rules
    # mod = import_module('lambdas-to-rules')
    # lambdas_to_rules = getattr(mod, 'lambdas_to_rules')
    # with open(ngrams_all, 'w') as f, redirect_stdout(f), redirect_stderr(log):
    #     lambdas_to_rules(freq_lex, rules_all)

    # # ngrams to rules
    # mod = import_module('ngrams-to-rules-me')
    # ngrams_to_rules = getattr(mod, 'ngrams_to_rules')
    # with open(rules, 'w') as f, redirect_stdout(f), redirect_stderr(log):
    #     ngrams_to_rules(ngrams_all)


def non_parallel_training(config, cache_dir, log):

    # MIN = 1

    # file names
    sl_tagged = os.path.join(
        cache_dir, f"{config['CORPUS']}.tagged.{config['SL']}")
    lines = os.path.join(cache_dir, f"{config['CORPUS']}.lines")
    tl_lm = f"{config['CORPUS']}.{config['SL']}-{config['TL']}.{config['TL']}.lm"
    # biltrans = os.path.join(cache_dir, f"{config['CORPUS']}.{config['SL']}-{config['TL']}.biltrans")
    ambig = os.path.join(cache_dir, f"{config['CORPUS']}.{config['SL']}-{config['TL']}.ambig")
    multi_trimmed = os.path.join(cache_dir, f"{config['CORPUS']}.{config['SL']}-{config['TL']}.multi-trimmed")
    ranked = os.path.join(cache_dir, f"{config['CORPUS']}.{config['SL']}-{config['TL']}.ranked")
    # annotated = os.path.join(cache_dir, f"{config['CORPUS']}.{config['SL']}-{config['TL']}.annotated")
    lex_freq = os.path.join(cache_dir, f"{config['CORPUS']}.{config['SL']}-{config['TL']}.freq")
    ngrams = os.path.join(cache_dir, f"{config['CORPUS']}.{config['SL']}-{config['TL']}.ngrams")
    patterns = os.path.join(cache_dir, f"{config['CORPUS']}.{config['SL']}-{config['TL']}.patterns")
    rules = f"{config['CORPUS']}.{config['SL']}-{config['TL']}.ngrams-lm-np.lrx"

    if os.path.isfile(rules):
        if not query(f"Do you want to overwrite '{rules}'"):
            print(f"(re)move {rules} and re-run lexical_training.py")
            exit(1)
        os.remove(rules)

    if 'TL_MODEL' not in config:
        if os.path.isfile(tl_lm):
            if not query(f"Do you want to overwrite '{tl_lm}'"):
                print(f"(re)move {tl_lm} or pass the lang model as input and re-run lexical_training.py")
                exit(1)
            os.remove(tl_lm)

    with open(config['CORPUS_SL'], 'r') as corpus_sl:
        training_lines = len(corpus_sl.readlines())
        if config['TRAINING_LINES'] > training_lines:
            print(
                f"Warning: {config['TRAINING_LINES']}(TRAINING_LINES) > {training_lines}")
        else:
            training_lines = config['TRAINING_LINES']

    print(f"Tagging {training_lines} lines from the source side corpus ...")
    cmds = [['head', '-n', str(training_lines)],
            ['apertium', '-d', config['LANG_DATA'],
             f"{config['SL']}-{config['TL']}-tagger"],
            ['apertium-pretransfer']]
    with open(config['CORPUS_SL']) as inp, open(sl_tagged, 'w') as outp:
        pipe(cmds, inp, outp, log, training_lines).wait()

    # removing lines with no analyses
    with open(lines, 'w') as f:
        call(['seq', '0', str(training_lines-1)],
             stdout=f, stderr=log)

    clean_tagged = os.path.join(
        cache_dir, f"{config['CORPUS']}.clean_tagged")
    with open(clean_tagged, 'w') as f1:
        cmds = [['paste', lines, sl_tagged],
                ['grep', '<*\t*<']]
        pipe(cmds, None, f1, log).wait()

    with open(clean_tagged, 'r') as f0:
        with open(lines, 'w') as f1:
            call(['cut', '-f', '1'], stdin=f0, stdout=f1, stderr=log)

        f0.seek(0)
        with open(sl_tagged, 'w') as f2:
            # cmds = [['cut', '-f', '2'], ['sed', 's/ /~~/g'],
            #         ['sed', 's/\$[^\^]*/$ /g']]
            # pipe(cmds, f0, f2, log).wait()
            call(['cut', '-f', '2'], stdin=f0, stdout=f2, stderr=log)

    os.remove(clean_tagged)

    if 'TL_MODEL' in config:
        tl_lm = config['TL_MODEL']
    else:
        print("Making a language model from all lines of " + config['CORPUS_TL']
              + " using IRSTLM from " + irstlm_path()
              + " and temporary files in ./tmp/")
        os.environ['IRSTLM'] = irstlm_path()
        call([os.path.join(irstlm_path(), 'bin/build-lm.sh'), '-i', config['CORPUS_TL'], '-o',
              tl_lm+'.gz', '-t', 'tmp'], stderr=log)

        with gzip.open(tl_lm+'.gz', 'rb') as f_in, open(tl_lm, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)

        os.remove(tl_lm+'.gz')
        # os.remove('tmp_tl')

    modes = get_modes(config['LANG_DATA'])
    sl_tl_autobil = get_autobil(modes, config['LANG_DATA'], config['PAIR'])
    mode_after_biltrans = get_mode_after_biltrans(modes, config['LANG_DATA'], config['PAIR'])

    print("Running multitrans ...")
    with open(sl_tagged) as f_in:
        with open(ambig, 'w') as f_out:
            cmds = [['multitrans', '-b', '-t', '-n', '-f', sl_tl_autobil]]
            pipe(cmds, f_in, f_out, log)

        f_in.seek(0)
        with open(multi_trimmed, 'w') as f_out:
            cmds = [['multitrans', '-m', '-t', '-f', sl_tl_autobil]]
            pipe(cmds, f_in, f_out, log)

    print("Running irstlm-ranker on mode after biltrans ...")
    with open(multi_trimmed) as f_in, open(ranked, 'w') as f_out:
        cmds = [p[0] + p[1] for p in mode_after_biltrans] + [['irstlm-ranker', tl_lm, multi_trimmed, '-f']]
        pipe(cmds, f_in, f_out, log).wait()

    # with open(annotated, 'w') as f_out:
    #     call(['paste', multi_trimmed, ranked], stdout=f_out, stderr=log)

    print("Turning ranked ngrams into rules ...")
    # extract frac freq
    mod = import_module('biltrans-extract-frac-freq')
    extract_frac_freq = getattr(mod, 'biltrans_extract_frac_freq')
    with open(lex_freq, 'w') as f, redirect_stdout(f), redirect_stderr(log):
        extract_frac_freq(ambig, ranked)

    # ngrams
    mod = import_module('biltrans-count-patterns-ngrams')
    count_patterns_ngrams = getattr(mod, 'biltrans_count_patterns_ngrams')
    with open(ngrams, 'w') as f, redirect_stdout(f), redirect_stderr(log):
        count_patterns_ngrams(ambig, ranked)

    # patterns
    mod = import_module('ngram-pruning-frac')
    ngram_pruning_frac = getattr(mod, 'ngram_pruning_frac')
    with open(patterns, 'w') as f, redirect_stdout(f), redirect_stderr(log):
        ngram_pruning_frac(lex_freq, ngrams)

    # extracting rules
    mod = import_module('ngrams-to-rules')
    ngrams_to_rules = getattr(mod, 'ngrams_to_rules')
    with open(rules, 'w') as f, redirect_stdout(f), redirect_stderr(log):
        ngrams_to_rules(patterns, config['CRISPHOLD'])
    return rules


def main(config_file):
    print("Validating configuration ...")
    config = check_config(config_file)

    # appending lex scripts' paths to environment path
    sys.path.insert(0, '/opt/local/share/apertium-lex-tools')
    sys.path.insert(0, '/usr/local/share/apertium-lex-tools')
    sys.path.insert(0, '/usr/share/apertium-lex-tools')  # Prefer /usr over /usr/local and /opt

    # remove after testing
    sys.path.insert(0, '../lex-tools/scripts')

    # cleaning the parallel corpus i.e. removing empty sentences, sentences only with '*', '.', or '°'
    # print("Cleaning corpus ...")
    # clean_corpus(config['CORPUS_SL'], config['CORPUS_TL'])

    cache_dir = f"cache-{config['CORPUS']}-{config['SL']}-{config['TL']}"

    if not config['IS_PARALLEL']:
        cache_dir = cache_dir + '-np'
    # the directory where all the intermediary outputs are stored
    if os.path.isdir(cache_dir):
        if not query(f"Do you want to overwrite the files in '{cache_dir}'"):
            print(f"(re)move {cache_dir} and re-run lexical_training.py")
            exit(0)
        shutil.rmtree(cache_dir)

    os.mkdir(cache_dir)

    log = os.path.join(cache_dir, "training.log")

    with open(log, 'a') as log_file:
        if config['IS_PARALLEL']:
            rules = parallel_training(config, cache_dir, log_file)
        else:
            rules = non_parallel_training(config, cache_dir, log_file)
    print(f"Training complete! Generated lrx file: {rules}")


if __name__ == '__main__':
    config_file = 'config.toml'
    if(len(sys.argv) == 2):
        config_file = sys.argv[1]
    main(config_file)
