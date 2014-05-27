import requests


def xml_to_exist(args, xml, wiki_id, page_id):
    """
    Sends xml to the desired exist-db endpoint
    :param args: an arg namespace -- allows flexible DI
    :type args:class:`argparse.Namespace`
    :param xml: the xml string
    :type xml: str
    :param wiki_id: the id of the wiki this page belongs to
    :type wiki_id: str
    :param page_id: the id of the page this is a parse of
    :type page_id: str
    """
    r = requests.put('%s/exist/%s/%s.xml' % (args.url, wiki_id, page_id),
                     data=str(xml),
                     headers={'Content-Type': 'application/xml', 'Content-Length': len(xml), 'Charset': 'utf-8'})
    print r.content, r.url
