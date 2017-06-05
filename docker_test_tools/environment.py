import os
import logging
import subprocess

import waiting
from contextlib import contextmanager

import config
from api_version import get_server_api_version

SEPARATOR = '|'
UNDERSCORE = '_'


class EnvironmentController(object):
    """Utility for managing environment operations."""

    def __init__(self, project_name, compose_path, log_path, reuse_containers=False):

        self.log_path = log_path
        self.compose_path = compose_path
        self.project_name = project_name
        self.reuse_containers = reuse_containers

        self.environment_variables = self._get_environment_variables()
        self.services = self.get_services()

        self.logs_file = None
        self.logs_process = None

    @classmethod
    def from_file(cls, config_path):
        """Return an environment controller based on the given config.

        :return EnvironmentController: controller based on the given config
        """
        config_object = config.Config(config_path=config_path)
        return cls(log_path=config_object.log_path,
                   project_name=config_object.project_name,
                   compose_path=config_object.docker_compose_path,
                   reuse_containers=config_object.reuse_containers)

    def get_services(self):
        """Get the services info based on the compose file.

        :return dict: of format {'service-name': check_callback}
        """
        logging.debug("Getting environment services, using docker compose: %s", self.compose_path)
        try:
            services_output = subprocess.check_output(
                ['docker-compose', '-f', self.compose_path, '-p', self.project_name, 'config', '--services'],
                stderr=subprocess.STDOUT, env=self.environment_variables
            )

        except subprocess.CalledProcessError as error:
            raise RuntimeError("Failed getting environment services, reason: %s" % error.output)

        return services_output.strip().split('\n')

    def setup(self):
        """Sets up the environment using docker commands.

        Should be called once before *all* the tests start.
        """
        try:
            logging.debug("Setting up the environment")
            self.cleanup()
            self.run_containers()
            self.start_log_collection()
        except:
            logging.exception("Setup failure, tearing down the test environment")
            self.teardown()
            raise

    def teardown(self):
        """Tears down the environment using docker commands.

        Should be called once after *all* the tests finish.
        """
        logging.debug("Tearing down the environment")
        try:
            self.stop_log_collection()
            self.split_logs()
        finally:
            self.cleanup()

    def cleanup(self):
        """Cleanup the environment.

        Kills and removes the environment containers.
        """
        if self.reuse_containers:
            logging.warning("Container reuse enabled: Skipping environment cleanup")
            return

        self.kill_containers()
        self.remove_containers()

    def run_containers(self):
        """Run environment containers."""
        logging.debug("Running environment containers, using docker compose: %s", self.compose_path)
        try:
            subprocess.check_output(
                ['docker-compose', '-f', self.compose_path, '-p', self.project_name, 'up', '--build', '-d'],
                stderr=subprocess.STDOUT, env=self.environment_variables
            )
        except subprocess.CalledProcessError as error:
            raise RuntimeError("Failed running environment containers, reason: %s" % error.output)

    def start_log_collection(self):
        """Start a log collection process which writes docker-compose logs into a file."""
        logging.debug("Starting logs collection from environment containers")
        self.logs_file = open(self.log_path, 'w')
        self.logs_process = subprocess.Popen(
            ['docker-compose', '-f', self.compose_path, '-p', self.project_name, 'logs', '--no-color', '-f', '-t'],
            stdout=self.logs_file, env=self.environment_variables
        )

    def stop_log_collection(self):
        """Stop the log collection process and close the log file."""
        logging.debug("Stopping logs collection from environment containers")
        if self.logs_process:
            self.logs_process.kill()
            self.logs_process.wait()

        if self.logs_file:
            self.logs_file.close()

    def split_logs(self):
        """Split the collected docker-compose log file into a file per service.

        Each line in the collected log file is in a format of: 'service.name_number  | message'
        This method writes each line to it's service log file amd keeps only the message.
        """
        logging.debug("Splitting log file into separated files per service")
        log_dir = os.path.dirname(self.log_path)
        services_log_files = {service_name: open(os.path.join(log_dir, service_name + '.log'), 'w')
                              for service_name in self.services}
        try:
            with open(self.log_path, 'r') as combined_log_file:
                for log_line in combined_log_file.readlines():
                    separator_location = log_line.find(SEPARATOR)
                    if separator_location != -1:
                        service_name = log_line[:log_line.rfind(UNDERSCORE, 0, separator_location)]
                        message = log_line[separator_location + 1:]
                        services_log_files[service_name].write(message)
        finally:
            [services_log_file.close() for services_log_file in services_log_files.itervalues()]

    def remove_containers(self):
        """Remove the environment containers."""
        logging.debug("Removing environment containers, using docker compose: %s", self.compose_path)
        try:
            subprocess.check_output(
                ['docker-compose', '-f', self.compose_path, '-p', self.project_name, 'rm', '-f'],
                stderr=subprocess.STDOUT, env=self.environment_variables
            )
        except subprocess.CalledProcessError as error:
            raise RuntimeError("Failed removing environment containers, reason: %s" % error.output)

    def kill_containers(self):
        """Kill the environment containers."""
        logging.debug("Killing environment containers, using docker compose: %s", self.compose_path)
        try:
            subprocess.check_output(
                ['docker-compose', '-f', self.compose_path, '-p', self.project_name, 'kill'],
                stderr=subprocess.STDOUT, env=self.environment_variables
            )
        except subprocess.CalledProcessError as error:
            raise RuntimeError("Failed running environment containers, reason: %s" % error.output)

    def kill_container(self, name):
        """Kill the container.

        :param str name: container name as it appears in the docker compose file.
        """
        self.validate_service_name(name)
        logging.debug("Killing %s container", name)
        try:
            subprocess.check_output(
                ['docker-compose', '-f', self.compose_path, '-p', self.project_name, 'kill', name],
                stderr=subprocess.STDOUT, env=self.environment_variables
            )
        except subprocess.CalledProcessError as error:
            raise RuntimeError("Failed killing container %s reason: %s" % (name, error.output))

    def restart_container(self, name):
        """Restart the container.

        :param str name: container name as it appears in the docker compose file.
        """
        self.validate_service_name(name)
        logging.debug("Restarting container %s", name)
        try:
            subprocess.check_output(
                ['docker-compose', '-f', self.compose_path, '-p', self.project_name, 'restart', name],
                stderr=subprocess.STDOUT, env=self.environment_variables
            )
        except subprocess.CalledProcessError as error:
            raise RuntimeError("Failed restarting container %s reason: %s" % (name, error.output))

    def pause_container(self, name):
        """Pause the container.

        :param str name: container name as it appears in the docker compose file.
        """
        self.validate_service_name(name)
        logging.debug("Pausing %s container", name)
        try:
            subprocess.check_output(
                ['docker-compose', '-f', self.compose_path, '-p', self.project_name, 'pause', name],
                stderr=subprocess.STDOUT, env=self.environment_variables
            )
        except subprocess.CalledProcessError as error:
            raise RuntimeError("Failed pausing container %s reason: %s" % (name, error.output))

    def unpause_container(self, name):
        """Unpause the container.

        :param str name: container name as it appears in the docker compose file.
        """
        self.validate_service_name(name)
        logging.debug("Unpausing %s container", name)
        try:
            subprocess.check_output(
                ['docker-compose', '-f', self.compose_path, '-p', self.project_name, 'unpause', name],
                stderr=subprocess.STDOUT, env=self.environment_variables
            )
        except subprocess.CalledProcessError as error:
            raise RuntimeError("Failed unpausing container %s reason: %s" % (name, error.output))

    def get_container_id(self, name):
        """Get container id by name.

        :param str name: container name as it appears in the docker compose file.
        """
        self.validate_service_name(name)
        try:
            return subprocess.check_output(
                ['docker-compose', '-f', self.compose_path, '-p', self.project_name, 'ps', '-q', name],
                stderr=subprocess.STDOUT, env=self.environment_variables
            )
        except subprocess.CalledProcessError as error:
            raise RuntimeError("Failed getting container %s id, reason: %s" % (name, error.output))

    def is_container_ready(self, name):
        """"Return True if the container is in ready state.

        If a health check is defined, a healthy container will be considered as ready.
        If no health check is defined, a running container will be considered as ready.

        :param str name: container name as it appears in the docker compose file.
        """
        self.validate_service_name(name)
        logging.debug("Getting %s container state", name)
        container_id = self.get_container_id(name)
        try:
            status_output = subprocess.check_output(
                r"docker inspect --format='{{json .State}}' " + container_id,
                shell=True, stderr=subprocess.STDOUT, env=self.environment_variables
            )

        except subprocess.CalledProcessError as error:
            logging.warning("Failed getting container %s state, reason: %s", name, error.output)
            return False

        if '"Health":' in status_output:
            is_ready = '"Status":"healthy"' in status_output
        else:
            is_ready = '"Status":"running"' in status_output

        logging.debug("Container %s ready: %s", name, is_ready)
        return is_ready

    def wait_for_services(self, services=None, interval=1, timeout=60):
        """Wait for the services checks to pass.

        If the service compose configuration contains an health check, the method will wait for a 'healthy' state.
        If it doesn't the method will wait for a 'running' state.
        """
        services = services if services else self.services
        logging.info('Waiting for %s to reach the required state', services)

        def service_checks():
            """Return True if services checks pass."""
            return all([self.is_container_ready(name) for name in services])

        try:
            waiting.wait(service_checks, sleep_seconds=interval, timeout_seconds=timeout)
            logging.info('Services %s reached the required state', services)
            return True

        except waiting.TimeoutExpired:
            logging.error('%s failed to to reach the required state', services)
            return False

    @contextmanager
    def container_down(self, name, interval=1, timeout=60):
        """Container down context manager.

        Simulate container down scenario by killing the container within the context,
        once context ends restart the container and wait for the service check to pass.

        :param str name: container name as it appears in the docker compose file.
        :param int interval: interval (in seconds) between checks.
        :param int timeout: timeout (in seconds) for all checks to pass.

        Usage:

        >>> with controller.service_down(name='consul'):
        >>>     # container will be down in this context
        >>>
        >>> # container will be back up after context end
        """
        self.validate_service_name(name)
        self.kill_container(name=name)
        try:
            yield
        finally:
            self.restart_container(name=name)
            self.wait_for_services(services=[name, ], interval=interval, timeout=timeout)

    @contextmanager
    def container_paused(self, name, interval=1, timeout=60):
        """Container pause context manager.

        pause the container within the context, once context ends un-pause the container and wait for 
        the service check to pass.

        :param str name: container name as it appears in the docker compose file.
        :param int interval: interval (in seconds) between checks.
        :param int timeout: timeout (in seconds) for all checks to pass.

        Usage:

        >>> with controller.container_paused(name='consul'):
        >>>     # container will be paused in this context
        >>>
        >>> # container will be back up after context end
        """
        self.validate_service_name(name)
        self.pause_container(name=name)
        try:
            yield
        finally:
            self.unpause_container(name=name)
            self.wait_for_services(services=[name, ], interval=interval, timeout=timeout)

    def validate_service_name(self, name):
        if name not in self.services:
            raise ValueError('Invalid service name: %r, must be one of %s' % (name, self.services))

    @staticmethod
    def _get_environment_variables():
        """Set the compose api version according to the server's api version"""
        server_api_version = get_server_api_version()
        logging.debug("docker server api version is %s, updating environment_variables", server_api_version)
        env = os.environ.copy()
        env['COMPOSE_API_VERSION'] = env['DOCKER_API_VERSION'] = server_api_version
        return env
