"""
Some shared functionality between all of the below scripts
"""

import logging
import numpy as np
import math
import time
from gensim.corpora import Dictionary
from gensim.matutils import corpus2dense
from nltk import SnowballStemmer, bigrams, trigrams
from nltk.corpus import stopwords
from collections import defaultdict
from boto.ec2 import connect_to_region


dictlogger = logging.getLogger('gensim.corpora.dictionary')
stemmer = SnowballStemmer('english')
english_stopwords = stopwords.words('english')
instances_launched = []
connection = None


def get_ec2_connection():
    global connection
    if not connection:
        connection = connect_to_region('us-west-2')
    return connection


def vec2dense(vector, num_terms):
    """Convert from sparse gensim format to dense list of numbers"""
    return list(corpus2dense([vector], num_terms=num_terms).T[0])


def normalize(phrase):
    global stemmer, english_stopwords
    nonstops_stemmed = [stemmer.stem(token) for token in phrase.split(' ') if token not in english_stopwords]
    return '_'.join(nonstops_stemmed).strip().lower()


def unis_bis_tris(string, prefix=''):
    unis = [normalize(word) for word in string.split(' ')]
    return (['%s%s' % (prefix, word) for word in unis]
            + ['%s%s' % (prefix, '_'.join(gram)) for gram in bigrams(unis)]
            + ['%s%s' % (prefix, '_'.join(gram)) for gram in trigrams(unis)])


def launch_lda_nodes(instance_count=20, ami="ami-40701570"):
    global instances_launched
    conn = get_ec2_connection()
    requests = conn.request_spot_instances('0.80', ami,
                                           key_name='LDA Node',
                                           count=instance_count,
                                           instance_type='m2.4xlarge',
                                           subnet_id='subnet-e4d087a2',
                                           security_group_ids=['sg-72190a10']
                                           )
    fulfilled = False
    while not fulfilled:
        time.sleep(15)
        requests = conn.get_all_spot_instance_requests(request_ids=[r.id for r in requests])
        if len(filter(lambda x: x.status == 'price-too-low', requests)) > 0:
            raise StandardError("Bid price too low -- try again later")
        fulfilled = len(filter(lambda x: x.status == 'fulfilled', requests)) == instance_count
    instance_ids = [instance.instance_id for instance in requests if instance.instance_id]
    instances_launched = conn.get_all_instances(instance_ids=instance_ids)


def terminate_lda_nodes():
    global instances_launched
    get_ec2_connection().terminate_instances(instance_ids=[instance.instance_id for instance in instances_launched])


def log(*args):
    """
    TODO: use a real logger
    """
    print args


def get_dct_and_bow_from_features(id_to_features):
    log("Extracting to dictionary...")
    documents = id_to_features.values()
    dct = WikiaDSTKDictionary(documents)
    dct.filter_stops(documents)

    log("---Bag of Words Corpus---")

    bow_docs = {}
    for name in id_to_features:
        sparse = dct.doc2bow(id_to_features[name])
        bow_docs[name] = sparse
    return dct, bow_docs


def write_csv_and_text_data(args, bucket, modelname, id_to_features, bow_docs, lda_model):
    # counting number of features so that we can filter
    tally = defaultdict(int)
    for name in id_to_features:
        vec = bow_docs[name]
        sparse = lda_model[vec]
        for (feature, frequency) in sparse:
            tally[feature] += 1

    # Write to sparse_csv here, excluding anything exceding our max frequency
    log("Writing topics to sparse CSV")
    sparse_csv_filename = modelname.replace('.model', '-sparse-topics.csv')
    text_filename = modelname.replace('.model', '-topic-features.csv')
    with open(args.path_prefix+sparse_csv_filename, 'w') as sparse_csv:
        for name in id_to_features:
            vec = bow_docs[name]
            sparse = dict(lda_model[vec])
            sparse_csv.write(",".join([str(name)]
                                      + ['%d-%.8f' % (n, sparse.get(n, 0))
                                         for n in range(args.num_topics)
                                         if tally[n] < args.max_topic_frequency])
                             + "\n")

    with open(args.path_prefix+text_filename, 'w') as text_output:
        text_output.write("\n".join(lda_model.show_topics(topics=args.num_topics, topn=15, formatted=True)))

    log("Uploading data to S3")
    csv_key = bucket.new_key(args.s3_prefix+sparse_csv_filename)
    csv_key.set_contents_from_file(args.path_prefix+sparse_csv_filename)
    text_key = bucket.new_key(args.s3_prefix+text_filename)
    text_key.set_contents_from_file(args.path_prefix+text_filename)


class WikiaDSTKDictionary(Dictionary):

    def filter_stops(self, documents, num_stops=300):
        """
        Uses statistical methods  to filter out stopwords
        See http://www.cs.cityu.edu.hk/~lwang/research/hangzhou06.pdf for more info on the algo
        """
        word_probabilities_summed = dict()
        num_documents = len(documents)
        for document in documents:
            doc_bow = self.doc2bow(document)
            sum_counts = sum([float(count) for _, count in doc_bow])
            for token_id, probability in [(token_id, float(count)/sum_counts) for token_id, count in doc_bow]:
                word_probabilities_summed[token_id] = word_probabilities_summed.get(token_id, []) + [probability]
        mean_word_probabilities = [(token_id, sum(probabilities)/num_documents)
                                   for token_id, probabilities in word_probabilities_summed.items()]

        # For variance of probability, using Numpy's variance metric, padding zeroes where necessary.
        # Should do the same job as figure (3) in the paper
        word_statistical_value_and_entropy = [(token_id,
                                               probability   # statistical value
                                               / np.var(probabilities + ([0] * (num_documents - len(probabilities)))),
                                               sum([prob * math.log(1.0/prob) for prob in probability])  # entropy
                                               )
                                              for token_id, probability in mean_word_probabilities]

        # Use Borda counts to combine the rank votes of statistical value and entropy
        sat_ranking = dict(
            map(lambda y: (y[1], y[0]),
                list(enumerate(map(lambda x: x[0],
                                   sorted(word_statistical_value_and_entropy, key=lambda x: x[1])))))
        )
        entropy_ranking = dict(
            map(lambda y: (y[1], y[0]),
                list(enumerate(map(lambda x: x[0],
                                   sorted(word_statistical_value_and_entropy, key=lambda x: x[2])))))
        )
        borda_ranking = sorted([(token_id, entropy_ranking[token_id] + sat_ranking[token_id])
                                for token_id in sat_ranking],
                               key=lambda x: x[1])

        dictlogger.info("keeping %i tokens, removing %i 'stopwords'" %
                        (len(borda_ranking) - num_stops, num_stops))

        # do the actual filtering, then rebuild dictionary to remove gaps in ids
        self.filter_tokens(good_ids=[token_id for token_id, _ in borda_ranking[num_stops:]])
        self.compactify()
        dictlogger.info("resulting dictionary: %s" % self)