#!/usr/bin/env python3
#Excavator.py

import os
import re
import sys
import json
import logging
import colorlog
import argparse
import xmltodict
from pprint import pprint
from datetime import datetime
from subprocess import check_output
from elasticsearch import Elasticsearch, helpers


##global vars##
status_details = {
	'time_start': '',
	'time_end': '',
	'files':{
		'successful': {
			'files': {
				#'name': 'error'
			},
			'count': 0
		},
		'failed': {
			'files': {
				#'name': 'error'
			}, 
			'count': 0
		}
	}
}
default_args = {
	'elasticsearch': {
		'ip': '127.0.0.1',
		'port': 9200
	},
	"date_fmts": [
		"%m/%d/%Y %H:%M:%S %p",
		"%m/%d/%Y %H:%M %p",
		"%Y-%m-%dT%H:%M:%S.%fZ"
	]
}
###


def setup_logger():
	formatter = colorlog.ColoredFormatter(
		"%(log_color)s%(asctime)s:%(levelname)s:%(message)s",
		datefmt='%D'
	)

	log = logging.getLogger()

	handler2 = logging.FileHandler('.output.log')
	handler = logging.StreamHandler()
	handler.setFormatter(formatter)
	log.addHandler(handler)
	log.addHandler(handler2)
	log.setLevel(logging.DEBUG)

	return log


def summarize():
	print("[INFO] Start Time: {}".format(status_details['time_start']))
	status_details["time_end"] = datetime.now()
	print("[INFO] End Time: {}".format(status_details['time_end']))
	print("[INFO] Files sucessfully ingested: {}".format(status_details['files']['successful']['count']))
	print("[INFO] Files failed during ingestion: {}".format(status_details['files']['failed']['count']))
	print("[INFO] Following is the list of successful files and logs count")
	pprint(status_details['files']['successful']['files'])
	print("[INFO] Following is the list of failed files")
	pprint(status_details['files']['failed']['files'])
	print("[INFO] Start Time: {}".format(status_details['time_start']))
	print("[INFO] End Time: {}".format(status_details['time_end']))
	print("[INFO] Time difference: {}".format(status_details["time_end"]-status_details["time_start"]))


#perform a sanity check on OS
def check_os(condition):
	if condition == "wevtutil":
		if os.name != 'nt':
			print('[INFO] OS: Not Windows\n[ERROR] Quitting!\n')
			exit()
		else:
			print('[INFO] OS: Windows\n[SUCCESS] All OK!\n')
	elif condition == "slashes":
		slash = '\\'
		if os.name != 'nt':
			slash = '/'
		return slash


def convert(path,file):
	print('[SUCCESS] ' + file)
	try:
		# check_output('wevtutil qe ' + path + check_os("slashes") + file + ' /lf:true /f:XML >> ' + path + check_os("slashes") + file + '.xml', shell=True)
		check_output('wevtutil qe "{0}{1}{2}" /lf:true /f:XML >> "{0}{1}{2}.xml"'.format(path, check_os('slashes'), file), shell=True)
		return True
	except Exception as exception:
		print('[INFO] ', exception)
		print('[ERROR] Unable to execute command!')
		return False


#convert evtx files to xml
def evt_to_xml(path,file):
	#check if running on windows
	conversion_success = False
	check_os("wevtutil")
	#define scope of files
	if file == '*':
		for file in os.listdir(path):
			if file.endswith('.evtx'):
				conversion_success = convert(path,file)
	else:
		conversion_success = convert(path,file)
	return conversion_success


def is_date(mstr):
		validate = False
		for fmt in default_args.get("date_fmts"):
			try:
				datetime.strptime(mstr, fmt)
				validate = True
				break
			except Exception as e: pass
				# print("[Warning] Exception {} occurred in is_date while validating date {}...".format(e, mstr))
				# print("[Warning] But continuing")
				# sys.exit()
		if not validate:
			print("[Warning] Exception occurred in is_date while validating date {}...".format(mstr))
		return validate


def get_date(mstr):
		valid_date = "-"
		validate = False
		for fmt in default_args.get("date_fmts"):
			try:
				valid_date = datetime.strptime(mstr, fmt)
				validate = True
				break
			except Exception as e: pass
				# print("[Warning] Exception {} occurred in get_date while returning date {}...".format(e, mstr))
				# print("[Warning] But continuing")
				# sys.exit()
		if not validate:
			print("[Warning] Exception occurred in is_date while validating date {}...".format(mstr))
		return valid_date


#correct structure of the data field
def correct_data_field_structure(event):
	data = {}
	try:
		if ('Data' in event['Event']['EventData']) and not (event['Event']['EventData']['Data'] == None):
			for field in range(0,len(event['Event']['EventData']['Data'])):
				field_name = event['Event']['EventData']['Data'][field]['@Name']
				already_done = False
				try:
					if 'time' in field_name.lower():
						# to parse strings containing ? in them
						temp = event['Event']['EventData']['Data'][field]['#text'].replace("?", "")
						# to parse strings containing nanoseconds in them
						if '.' in temp and temp[-1] == 'Z' and len(temp) - temp.index('.') - 2 == 9 : temp = temp[:temp.index('.') + 7] + 'Z'
						if is_date(temp):
							text = get_date(temp)
							already_done = True
				except: pass
				if not already_done:
					try: text = event['Event']['EventData']['Data'][field]['#text']
					except: text = '-'
				data[field_name] = text
	except:
		return event
	event['Event']['EventData']['Data'] = data	
	return event


def validate_event(event):
	#print the log that is parsed from XML before editing anything
	if ('EventData' in event['Event']) and not (event['Event']['EventData'] == None):
		if ('Data' in event['Event']['EventData']) and not (event['Event']['EventData']['Data'] == None):
			if not ('@Name' in event['Event']['EventData']['Data']):
				try:
					event['Event']['EventData']['Data'][0]['@Name']
				except:
					group_data = [{'@Name': 'param1', '#text': str(event['Event']['EventData']['Data'])}]
					event['Event']['EventData']['Data'] = group_data
	if ('System' in event['Event']) and not (event['Event']['System'] == None):
		if ('EventID' in event['Event']['System']) and not (event['Event']['System'] == None):
			try:
				event['Event']['System']['EventID']['@Qualifiers']
			except:
				group_data = {'@Qualifiers': 'Unknown', '#text': event['Event']['System']['EventID']}
				event['Event']['System']['EventID'] = group_data
	return event


def push_to_elk(ip,port,index,user,pwd,bulk,scheme):
	elk = None
	if(user == None) and (pwd == None):
		elk = Elasticsearch(ip,scheme=scheme,port=port,)
	else:
		elk = Elasticsearch(ip,http_auth=(user,pwd),scheme=scheme,port=port,)
	try:
		helpers.bulk(elk, bulk)
		return True
	except Exception as exception:
		print('[INFO] ELK ingestion error')
		print(exception)
		return False


def send_now(ip,port,index,user,pwd,bulk,scheme):
	logs_sent = False
	#keep looping until the bulked logs have not been sent successfully
	while not logs_sent:
		logs_sent = push_to_elk(ip,port,index,user,pwd,bulk,scheme)
		if not logs_sent:
			continue
		else:
			return []


def process_file(action,path,ip,port,file,index,user,pwd,size,scheme):
	bulk = []
	successful_events = 0
	fileName = file.split('.')[0]

	with open(path+check_os("slashes")+file,'r', encoding='iso-8859-15') as opened_file:
		eventlog_maker = ""
		for line in opened_file:
			# Joins all broken XML parts to form one complete eventlog!
			if not ('<Event' in eventlog_maker and '</Event>' in eventlog_maker):
				try:
					line = line.replace("\n","")
					line = line.replace("\t","")
					line = line.replace("\r","")
					eventlog_maker+=line
					if not ('<Event' in eventlog_maker and '</Event>' in eventlog_maker):
						continue
				except Exception as exception:
					print(f'[ERROR] Exception {exception} was generated while making a complete log from file {file}')
					print(f'[INFO] During the conversion, the following line caused issue {line}')
					status_details['files']['failed']['count'] += 1
					status_details['files']['failed']['files'][file] = exception
			eventlog = eventlog_maker
			eventlog_maker = ""
			try:
				eventlog = xmltodict.parse(eventlog)
			except Exception as exception:
				print(f'[ERROR] Exception {exception} was generated while converting log to dict type from {file}')
				print(f'[INFO] During the conversion, the following log caused issue {eventlog}')
				status_details['files']['failed']['count'] += 1
				status_details['files']['failed']['files'][file] = exception
				with open(index+"-"+fileName+"-logs.xml", "a") as crashFileHandle:
					crashFileHandle.write(eventlog+"\n")
				continue
							
			eventlog = json.loads(json.dumps(eventlog))
			eventlog = validate_event(eventlog)
			eventlog = correct_data_field_structure(eventlog)
			eventlog['file_name'] = file
			successful_events=successful_events+1
			if action == 'send' or action == 'auto':
				bulk.append({
					"_index": index,
					"_type": index,
					"@timestamp": eventlog['Event']['System']['TimeCreated']['@SystemTime'],
					"body": eventlog
					})
				if (len(bulk) == size):
					print(f'[INFO] Time Passed: {datetime.now()-status_details["time_start"]} -- Sending Logs from {file} to ELK: {successful_events}')
					bulk = send_now(ip,port,index,user,pwd,bulk,scheme)
			elif action == 'json':
				print(json.dumps(eventlog, indent=4))
	status_details['files']['successful']['count'] += 1
	status_details['files']['successful']['files'][file] = successful_events
	print(f'[INFO] Elapsed Time: {datetime.now()-status_details["time_start"]} -- Sending Logs from {file} to ELK: {successful_events}')
	bulk = send_now(ip,port,index,user,pwd,bulk,scheme)
	print('[SUCCESS] Successfully processed the logs of file')


def xml_to_json_to_es(action,path,ip,port,file,index,user,pwd,size,scheme):
	#define scope of files for converting xml to json
	if file == '*':
		for file in os.listdir(path):
			if file.endswith('.xml'):
				process_file(action,path,ip,port,file,index,user,pwd,size,scheme)
	else:
		if file.endswith('.xml'):
			process_file(action,path,ip,port,file,index,user,pwd,size,scheme)


def process(action,path,ip,port,file,index,user,pwd,size,scheme):
	index = index.lower()
	if action == 'xml':
		evt_to_xml(path,file)
	if (action == 'send') or (action == 'json'):
		xml_to_json_to_es(action,path,ip,port,file,index,user,pwd,size,scheme)
	if (action == 'auto'):
		print("[CAUTION] AUTO only works with windows!")
		evt_to_xml(path,file)
		if not file == '*':
			if not file.endswith('.xml'):
				file = file + '.xml'
		xml_to_json_to_es(action,path,ip,port,file,index,user,pwd,size,scheme)


#Perform a sanity check on log path and IP address provided by user
def sanity_check(action,path,ip,file,scheme):
	if not action or (action != 'xml' and action != 'send' and action != 'json' and action != 'auto'):
		print('[ERROR] Please specify a valid action i.e. xml, send, json, auto')
		exit()
	if not path:
		print('[ERROR] Excavator needs to know the path to logs')
		exit()
	elif not os.path.isdir(''+path):
		print('[ERROR] Specified path does not exist')
		exit()
	if file != '*':
		if not os.path.isfile(path + check_os("slashes") + file):
			print('[ERROR] Specified file does not exist')
			exit()
	if not ip and (action=='auto' or action=='send'):
		print('[ERROR] IP not specified')
		exit()
	elif ip:
		sanity = re.match(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}", ip)
		if not (bool(sanity) and all(map(lambda n: 0 <= int(n) <= 255, sanity.groups()))):
			print('[ERROR] Invalid IP address!')
			exit()
	if scheme and not (scheme == 'http' or scheme == 'https'):
		print('[ERROR] Invalid scheme!')
		exit()


#main
if __name__ == '__main__':
	# logger = setup_logger()
	status_details["time_start"] = datetime.now()
	print(f'[INFO] Time of start: {status_details["time_start"]}')
	parser = argparse.ArgumentParser('Excavator.py')
	parser.add_argument('-m', metavar='<action>', type=str, help='auto, json, send, xml')
	parser.add_argument('-p', metavar='<path>', type=str, help='path to Evtx files')
	parser.add_argument('-ip', metavar='<ip>', default=default_args.get('elasticsearch').get('ip'), help='elasticsearch IP. Default is {}'.format(default_args.get('elasticsearch').get('ip')))
	parser.add_argument('-port', metavar='<port>', type=int, default=default_args.get('elasticsearch').get('port'), help='elasticsearch port. Default is {}'.format(default_args.get('elasticsearch').get('port')))
	parser.add_argument('-f', metavar='<file>', type=str, default='*', help='evtx file to process. Only use for single file')
	parser.add_argument('-i', metavar='<index>', type=str, default='excavator', help='name of ELK index')
	parser.add_argument('-user', metavar='<user>', type=str, help='username of ELK for authorization')
	parser.add_argument('-pwd', metavar='<pass>', type=str, help='password of ELK for authorization')
	parser.add_argument('-s', metavar='<size>', type=int, default=100, help='size of queue, default=100')
	parser.add_argument('-scheme', metavar='<size>', type=str, default='http', help='http or https')
	if len(sys.argv) <= 1:
		parser.print_help()
		exit()
	args = parser.parse_args()
	sanity_check(args.m,args.p,args.ip,args.f,args.scheme)
	process(args.m,args.p,args.ip,args.port,args.f,args.i,args.user,args.pwd,args.s,args.scheme)
	summarize()