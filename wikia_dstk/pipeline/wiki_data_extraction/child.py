import json
import os
import sys
import traceback
from boto import connect_s3
from boto.exception import S3ResponseError

from nlp_services.caching import use_caching
from nlp_services.discourse import AllEntitiesSentimentAndCountsService
from nlp_services.discourse.entities import TopEntitiesService, EntityDocumentCountsService, WpTopEntitiesService, WpEntityDocumentCountsService
from nlp_services.discourse.sentiment import WikiEntitySentimentService, WpWikiEntitySentimentService
from nlp_services.syntax import TopHeadsService

sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)
BUCKET = connect_s3().get_bucket('nlp-data')

# Get absolute path
BASE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

# Load serialized services into memory
with open(os.path.join(BASE_PATH, 'config/services-config.json')) as f:
    SERVICES = json.loads(f.read())['wiki-services']

caching_dict = dict([(service+'.get', {'write_only': True}) for service in SERVICES])
use_caching(per_service_cache=caching_dict)

def process_wiki(wid):
    print 'Calling wiki-level services on %s' % wid
    try:
        for service in SERVICES:
            try:
                print wid, service
                getattr(sys.modules[__name__], service)().get(wid)
                caching_dict[service+'.get'] = {'dont_compute': True}  # DRY fool!
                use_caching(per_service_cache=caching_dict)
            except KeyboardInterrupt:
                sys.exit()
            except Exception as e:
                print 'Could not call %s on %s!' % (service, wid)
                print traceback.format_exc()
    except:
        print "Problem with", wid
        exc_type, exc_value, exc_traceback = sys.exc_info()
        print "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))

process_wiki(sys.argv[1])
