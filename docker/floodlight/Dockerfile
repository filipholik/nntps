FROM eclipse-temurin:8-jdk

RUN apt-get update && \
    apt-get install -y ant && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt/floodlight

RUN git clone --branch v1.2 https://github.com/filipholik/floodlight.git /opt/floodlight

RUN ant

EXPOSE 6653
EXPOSE 8080

CMD ["java", "-jar", "target/floodlight.jar"]
