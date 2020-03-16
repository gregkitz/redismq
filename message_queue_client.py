import json
import logging
import os
import threading
import uuid

import grpc

from message_queue_pb2 import SendRequest, ReceiveRequest, GetRequest, AcknowledgeRequest
from message_queue_pb2_grpc import MessageQueueStub


def send_message(service_name, func_name, params={}, rsp_to=1):
    """
    Put a message into a queue.

    :param service_name: The name of the service.
    :param func_name: The name fo the function.
    :param params: The parameters.
    :param rsp_to: An optional timeout (in seconds) to wait for the response, defaults to 1.
    :return: The payload of the response.
    """
    logging.debug(f'sending message to {service_name}.{func_name} ...')
    msg_id = MQ.send_msg(service_name, func_name, json.dumps({
        "params": params
    }))

    logging.debug(f'receiving response from {service_name}.{func_name} ...')
    rsp = MQ.recv_rsp(service_name, func_name, msg_id, rsp_to)
    if not rsp:
        raise TimeoutError('{}.{}'.format(service_name, func_name))

    MQ.ack_rsp(service_name, func_name, msg_id, rsp)

    return json.loads(rsp)


class Consumers(object):
    """
    Consumers class.

    Helper class to handle multiple consumers.
    """

    def __init__(self, service_name, handler_funcs, timeout=1):
        """
        :param service_name: A service name.
        :param handler_funcs: A list of handler functions.
        :param timeout: An optional timeout in seconds, defaults to 1.
        """
        self.service_name = service_name
        self.handler_funcs = handler_funcs
        self.timeout = timeout
        self.threads = [threading.Thread(target=self._run,
                                         name='{}.{}'.format(service_name, m.__name__),
                                         args=(m, )) for m in handler_funcs]
        self.running = False

    def start(self):
        """
        Spawn a consumer thread for all handler functions.

        :return: None
        """
        self.running = True
        [t.start() for t in self.threads]

        return self

    def wait(self):
        """
        Wait for all consumer threads to finsih. N.B. This is a blocking operation.

        :return: None
        """
        [t.join() for t in self.threads]

        return self

    def stop(self):
        """
        Stop all consumer threads.

        :return: None
        """
        self.running = False

        return self

    def _run(self, handler_func):

        while self.running:

            logging.debug(f'receiving message in {self.service_name}.{handler_func.__name__} ...')

            msg_id, msg_payload = MQ.recv_msg(self.service_name, handler_func.__name__, self.timeout)
            if not msg_payload:
                continue

            try:
                params = json.loads(msg_payload)['params']
            except Exception as e:
                rsp = {
                    "error": "Error parsing received payload ({}): {}".format(e.__class__.__name__, str(e))
                }
            else:
                logging.debug(f'calling handler function in {self.service_name}.{handler_func.__name__} ...')
                try:
                    rsp = handler_func(params)
                except Exception as e:
                    rsp = {
                        "error": "Error calling handler function ({}) in {}.{}: {}".format(e.__class__.__name__,
                                                                                           self.service_name,
                                                                                           handler_func.__name__,
                                                                                           str(e))
                    }

            MQ.ack_msg(self.service_name, handler_func.__name__, msg_id)

            logging.debug(f'sending response from {self.service_name}.{handler_func.__name__} ...')

            MQ.send_rsp(self.service_name, handler_func.__name__, msg_id, json.dumps(rsp))


class MessageQueue(object):
    """
    Message Queue class.
    """

    def __init__(self):
        host, port = os.getenv('MESSAGE_QUEUE_HOSTNAME', 'localhost'), os.getenv('MESSAGE_QUEUE_PORTNR', '50051')
        self.channel = grpc.insecure_channel('{}:{}'.format(host, port))
        self.stub = MessageQueueStub(self.channel)
        self.subscribers = {}

    def __del__(self):
        self.channel.close()

    def send_msg(self, service_name, func_name, payload, msg_id=None):
        """
        Send a message.

        :param service_name: The remote service to call.
        :param func_name: The remote function to call.
        :param payload: The message payload.
        :param msg_id: The ID of the message, may be None.
        :return: The message ID.
        """
        response = self.stub.send_msg(
            SendRequest(
                service_name=service_name,
                func_name=func_name,
                msg_id=msg_id or str(uuid.uuid4()),
                payload=payload,
            ))

        return response.msg_id

    def recv_rsp(self, service_name, func_name, msg_id, timeout=0):
        """
        Receive a response.

        :param service_name: The remote service to call.
        :param func_name: The remote function to call.
        :param msg_id: The message ID, may be None.
        :param timeout: The timeout in seconds.
        :return: The payload of the response.
        """
        response = self.stub.recv_rsp(
            ReceiveRequest(
                service_name=service_name,
                func_name=func_name,
                msg_id=msg_id,
                timeout=timeout
            ))

        return response.payload

    def get_rsp(self, service_name, func_name, msg_id):
        """
        Get a response.

        :param service_name: The remote service to call.
        :param func_name: The remote function to call.
        :param msg_id: The message ID, may be None.
        :return: The payload of the response.
        """
        response = self.stub.get_rsp(
            GetRequest(
                service_name=service_name,
                func_name=func_name,
                msg_id=msg_id,
            ))

        return response.payload

    def ack_rsp(self, service_name, func_name, msg_id, payload):
        """
        Acknowledge a response.

        :param service_name: The remote service to call.
        :param func_name: The remote function to call.
        :param msg_id: The message ID.
        :param payload: The payload of the response.
        :return: Success.
        """
        response = self.stub.ack_rsp(
            AcknowledgeRequest(
                service_name=service_name,
                func_name=func_name,
                msg_id=msg_id,
                payload=payload,
            ))

        return response.success

    def recv_msg(self, service_name, func_name, timeout=0):
        """
        Receive a message.

        :param service_name: The remote service to call.
        :param func_name: The remote function to call.
        :param timeout: The timeout in seconds.
        :return: The message payload.
        """
        response = self.stub.recv_msg(
            ReceiveRequest(
                service_name=service_name,
                func_name=func_name,
                msg_id=None,
                timeout=timeout
            ))

        return response.msg_id, response.payload

    def get_msg(self, service_name, func_name):
        """
        Get a message.

        :param service_name: The remote service to call.
        :param func_name: The remote function to call.
        :return: The message ID and payload.
        """
        response = self.stub.get_msg(
            GetRequest(
                service_name=service_name,
                func_name=func_name,
                msg_id=None,
            ))

        return response.msg_id, response.payload

    def ack_msg(self, service_name, func_name, msg_id):
        """
        Acknowledge a message.

        :param service_name: The remote service to call.
        :param func_name: The remote function to call.
        :param msg_id: The message ID.
        :return: Success.
        """
        response = self.stub.ack_msg(
            AcknowledgeRequest(
                service_name=service_name,
                func_name=func_name,
                msg_id=msg_id,
                payload=None,
            ))

        return response.success

    def send_rsp(self, service_name, func_name, msg_id, payload):
        """
        Send a response.

        :param service_name: The remote service to call.
        :param func_name: The remote function to call.
        :param msg_id: The message ID.
        :param payload: The payload of the response.
        :return: Success.
        """
        response = self.stub.send_rsp(
            SendRequest(
                service_name=service_name,
                func_name=func_name,
                msg_id=msg_id,
                payload=payload,
            ))

        return response.msg_id


MQ = MessageQueue()
