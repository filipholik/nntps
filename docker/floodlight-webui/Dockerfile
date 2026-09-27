FROM eclipse-temurin:8-jdk

RUN apt-get update && \
    apt-get install -y ant && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt/floodlight

RUN git clone --branch v1.2 https://github.com/filipholik/floodlight.git /opt/floodlight
# Replace the old Floodlight web UI with the richer floodlight-webui
RUN rm -rf /opt/floodlight/src/main/resources/web
RUN git clone --branch main https://github.com/filipholik/floodlight-webui.git /opt/floodlight/src/main/resources/web

RUN ant

EXPOSE 6653
EXPOSE 8080

CMD ["java", "-jar", "target/floodlight.jar"]