TOPO := mpls-l3vpn.clab.yml
LAB  := mpls-l3vpn

.PHONY: help up deploy bootstrap verify shell logs status down clean

help:
	@echo "MPLS L3VPN lab"
	@echo ""
	@echo "  make up        deploy, bootstrap the nodes, then verify"
	@echo "  make verify    re-run the checks against a running lab"
	@echo "  make status    containerlab node table"
	@echo "  make shell N=pe1   open vtysh on a node"
	@echo "  make logs  N=pe1   FRR logs for a node"
	@echo "  make down      destroy the lab"

up: deploy bootstrap verify

deploy:
	@echo "==> Deploying $(LAB)"
	sudo containerlab deploy -t $(TOPO)

bootstrap:
	@echo "==> Bootstrapping nodes"
	@sudo bash scripts/bootstrap-nodes.sh

verify:
	@sudo python3 scripts/verify.py

status:
	@sudo containerlab inspect -t $(TOPO)

shell:
	@test -n "$(N)" || (echo "usage: make shell N=pe1"; exit 1)
	@sudo docker exec -it clab-$(LAB)-$(N) vtysh

logs:
	@test -n "$(N)" || (echo "usage: make logs N=pe1"; exit 1)
	@sudo docker logs clab-$(LAB)-$(N)

down:
	sudo containerlab destroy -t $(TOPO) --cleanup

clean: down
	@rm -rf clab-$(LAB) .$(LAB).clab.yml.bak
